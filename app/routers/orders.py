"""Заказы: создание, свои заказы, лента водителя, назначенные, отмена, завершение."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.deps import require_driver, require_passenger
from app.models import Offer, OfferStatus, Order, OrderStatus, User, utcnow
from app.schemas import OfferOut, OrderIn, OrderOut
from app.sse import bus, publish_to_drivers

router = APIRouter(prefix="/orders", tags=["orders"])


def get_order_or_404(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Заказ не найден"
        )
    return order


def ensure_order_owner(order: Order, user: User) -> None:
    if order.passenger_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Это не ваш заказ"
        )


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(
    data: OrderIn,
    user: User = Depends(require_passenger),
    db: Session = Depends(get_db),
) -> Order:
    order = Order(
        passenger_id=user.id,
        from_point=data.from_point,
        to_point=data.to_point,
        price=data.price,
        status=OrderStatus.open,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    publish_to_drivers(
        db,
        "new_order",
        {
            "id": order.id,
            "from_point": order.from_point,
            "to_point": order.to_point,
            "price": order.price,
        },
    )
    return order


@router.get("/my", response_model=list[OrderOut])
def my_orders(
    user: User = Depends(require_passenger),
    db: Session = Depends(get_db),
) -> list[Order]:
    return list(
        db.scalars(
            select(Order)
            .where(Order.passenger_id == user.id)
            .order_by(Order.created_at.desc())
        ).all()
    )


@router.get("/feed", response_model=list[OrderOut])
def driver_feed(
    user: User = Depends(require_driver),
    db: Session = Depends(get_db),
) -> list[OrderOut]:
    orders = db.scalars(
        select(Order)
        .where(Order.status == OrderStatus.open)
        .options(selectinload(Order.offers).selectinload(Offer.driver))
        .order_by(Order.created_at.desc())
    ).all()

    result: list[OrderOut] = []
    for order in orders:
        order_out = OrderOut.model_validate(order)
        my_offer = next((o for o in order.offers if o.driver_id == user.id), None)
        if my_offer is not None:
            order_out.my_offer = OfferOut.model_validate(my_offer)
        result.append(order_out)
    return result


@router.get("/assigned", response_model=list[OrderOut])
def assigned_orders(
    user: User = Depends(require_driver),
    db: Session = Depends(get_db),
) -> list[Order]:
    """Заказы, где принят отклик именно этого водителя."""
    query = (
        select(Order)
        .join(Offer, Order.assigned_offer_id == Offer.id)
        .where(Offer.driver_id == user.id)
        .order_by(Order.updated_at.desc())
    )
    return list(db.scalars(query).all())


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(
    order_id: int,
    user: User = Depends(require_passenger),
    db: Session = Depends(get_db),
) -> Order:
    order = get_order_or_404(db, order_id)
    ensure_order_owner(order, user)

    pending_offers = list(
        db.scalars(
            select(Offer).where(
                Offer.order_id == order_id, Offer.status == OfferStatus.pending
            )
        ).all()
    )
    notifications = [
        (offer.driver_id, {"order_id": order_id, "offer_id": offer.id, "accepted": False})
        for offer in pending_offers
    ]

    # Атомарный переход open -> cancelled: второй параллельный cancel получит 409.
    result = db.execute(
        update(Order)
        .where(Order.id == order_id, Order.status == OrderStatus.open)
        .values(status=OrderStatus.cancelled, updated_at=utcnow())
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заказ уже нельзя отменить: он не в статусе open",
        )

    db.execute(
        update(Offer)
        .where(Offer.order_id == order_id, Offer.status == OfferStatus.pending)
        .values(status=OfferStatus.rejected)
    )
    db.commit()
    db.refresh(order)

    for driver_id, payload in notifications:
        bus.publish(driver_id, "offer_decision", payload)
    publish_to_drivers(db, "order_closed", {"order_id": order_id, "reason": "cancelled"})
    return order


@router.post("/{order_id}/complete", response_model=OrderOut)
def complete_order(
    order_id: int,
    user: User = Depends(require_driver),
    db: Session = Depends(get_db),
) -> Order:
    order = get_order_or_404(db, order_id)

    offer = db.get(Offer, order.assigned_offer_id) if order.assigned_offer_id else None
    if offer is None or offer.driver_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Заказ назначен не вам",
        )

    # Атомарный переход assigned -> completed.
    result = db.execute(
        update(Order)
        .where(Order.id == order_id, Order.status == OrderStatus.assigned)
        .values(status=OrderStatus.completed, updated_at=utcnow())
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Завершить можно только заказ в статусе assigned",
        )

    db.commit()
    db.refresh(order)

    bus.publish(order.passenger_id, "order_completed", {"order_id": order.id})
    return order
