"""SQLAlchemy-модели: User, Order, Offer.

Pydantic-схемы живут отдельно (schemas.py) и БД напрямую не трогают.
"""
import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """Единая точка времени для всех created_at/updated_at (UTC)."""
    return datetime.now(timezone.utc)


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Храним в БД значения ('open'), а не имена членов enum ('OPEN')."""
    return [member.value for member in enum_cls]


class Role(str, enum.Enum):
    passenger = "passenger"
    driver = "driver"


class OrderStatus(str, enum.Enum):
    open = "open"
    assigned = "assigned"
    cancelled = "cancelled"
    completed = "completed"


class OfferStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            name="user_role",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )

    orders: Mapped[list["Order"]] = relationship(
        back_populates="passenger",
        foreign_keys="Order.passenger_id",
    )
    offers: Mapped[list["Offer"]] = relationship(
        back_populates="driver",
        foreign_keys="Offer.driver_id",
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    passenger_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_point: Mapped[str] = mapped_column(String(255), nullable=False)
    to_point: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=OrderStatus.open,
    )
    # Обратная ссылка на принятый отклик (циклический FK orders <-> offers).
    assigned_offer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    passenger: Mapped["User"] = relationship(
        back_populates="orders", foreign_keys=[passenger_id]
    )
    offers: Mapped[list["Offer"]] = relationship(
        back_populates="order",
        foreign_keys="Offer.order_id",
        cascade="all, delete-orphan",
    )
    assigned_offer: Mapped[Optional["Offer"]] = relationship(
        foreign_keys=[assigned_offer_id], post_update=True
    )


class Offer(Base):
    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("order_id", "driver_id", name="uq_offers_order_driver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    driver_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    eta_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[OfferStatus] = mapped_column(
        Enum(
            OfferStatus,
            name="offer_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=OfferStatus.pending,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    order: Mapped["Order"] = relationship(
        back_populates="offers", foreign_keys=[order_id]
    )
    driver: Mapped["User"] = relationship(
        back_populates="offers", foreign_keys=[driver_id]
    )
