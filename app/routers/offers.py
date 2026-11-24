"""Отклики: список, создание водителем и принятие пассажиром."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.deps import require_driver, require_passenger
from app.models import Offer, OfferStatus, Order, OrderStatus, User, utcnow
from app.routers.orders import ensure_order_owner, get_order_or_404
from app.schemas import OfferIn, OfferOut, OrderOut
from app.sse import bus, publish_to_drivers

router = APIRouter(prefix="/orders", tags=["offers"])


@router.get("/{order_id}/offers", response_model=list[OfferOut])
def list_offers(
    order_id: int,
    user: User = Depends(require_passenger),
    db: Session = Depends(get_db),
) -> list[Offer]:
    order = get_order_or_404(db, order_id)
    ensure_order_owner(order, user)

    return list(
        db.scalars(
            select(Offer)
            .where(Offer.order_id == order_id)
            .options(selectinload(Offer.driver))
            .order_by(Offer.price.asc(), Offer.eta_minutes.asc())
        ).all()
    )


@router.post(
    "/{order_id}/offers",
    response_model=OfferOut,
    status_code=status.HTTP_201_CREATED,
)
def create_offer(
    order_id: int,
    data: OfferIn,
    user: User = Depends(require_driver),
    db: Session = Depends(get_db),
) -> Offer:
    order = get_order_or_404(db, order_id)

    if order.status != OrderStatus.open:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заказ уже не открыт, откликнуться нельзя",
        )

    duplicate = db.scalar(
        select(Offer).where(Offer.order_id == order_id, Offer.driver_id == user.id)
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Вы уже откликнулись на этот заказ",
        )

    offer = Offer(
        order_id=order_id,
        driver_id=user.id,
        price=data.price,
        eta_minutes=data.eta_minutes,
        status=OfferStatus.pending,
    )
    db.add(offer)
    try:
        db.commit()
    except IntegrityError:
        # unique constraint (order_id, driver_id) ловит гонку двух откликов.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Вы уже откликнулись на этот заказ",
        )
    db.refresh(offer)

    bus.publish(
        order.passenger_id,
        "new_offer",
        {
            "order_id": order_id,
            "offer": {
                "id": offer.id,
                "driver_id": offer.driver_id,
                "driver_name": user.username,
                "price": offer.price,
                "eta_minutes": offer.eta_minutes,
            },
        },
    )
    return offer


@router.post("/{order_id}/offers/{offer_id}/accept", response_model=OrderOut)
def accept_offer(
    order_id: int,
    offer_id: int,
    user: User = Depends(require_passenger),
    db: Session = Depends(get_db),
) -> Order:
    order = get_order_or_404(db, order_id)
    ensure_order_owner(order, user)

    offer = db.get(Offer, offer_id)
    if offer is None or offer.order_id != order_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Отклик не найден"
        )

    if offer.status != OfferStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Отклик уже обработан"
        )

    # Атомарно занимаем заказ. Двойной accept: второй UPDATE не найдёт open-строку.
    result = db.execute(
        update(Order)
        .where(Order.id == order_id, Order.status == OrderStatus.open)
        .values(
            status=OrderStatus.assigned,
            assigned_offer_id=offer.id,
            updated_at=utcnow(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заказ уже не в статусе open",
        )

    accepted_offer_id = offer.id
    accepted_driver_id = offer.driver_id

    db.execute(
        update(Offer).where(Offer.id == accepted_offer_id).values(status=OfferStatus.accepted)
    )

    rejected_offers = list(
        db.scalars(
            select(Offer).where(
                Offer.order_id == order_id,
                Offer.id != accepted_offer_id,
                Offer.status == OfferStatus.pending,
            )
        ).all()
    )
    notifications = [(o.driver_id, o.id) for o in rejected_offers]

    if notifications:
        db.execute(
            update(Offer)
            .where(
                Offer.order_id == order_id,
                Offer.id != accepted_offer_id,
                Offer.status == OfferStatus.pending,
            )
            .values(status=OfferStatus.rejected)
        )
    db.commit()
    db.refresh(order)

    bus.publish(
        accepted_driver_id,
        "offer_decision",
        {"order_id": order_id, "offer_id": accepted_offer_id, "accepted": True},
    )
    for driver_id, rejected_id in notifications:
        bus.publish(
            driver_id,
            "offer_decision",
            {"order_id": order_id, "offer_id": rejected_id, "accepted": False},
        )
    publish_to_drivers(db, "order_closed", {"order_id": order_id, "reason": "assigned"})
    return order
