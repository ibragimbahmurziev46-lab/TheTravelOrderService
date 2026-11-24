"""Pydantic-схемы запросов и ответов (БД напрямую не трогают)."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import AliasPath, BaseModel, ConfigDict, Field, field_validator

from app.models import OfferStatus, OrderStatus, Role


class OrmModel(BaseModel):
    """Базовая схема ответа: читается из ORM-объекта, время всегда в UTC."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("created_at", "updated_at", check_fields=False, mode="after")
    @classmethod
    def _ensure_utc(cls, value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)


# --- Авторизация ---------------------------------------------------------


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[\w.-]+$")
    password: str = Field(min_length=6, max_length=72)
    role: Role


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=72)


class UserOut(OrmModel):
    id: int
    username: str
    role: Role


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    role: Role


# --- Заказы --------------------------------------------------------------


class OrderIn(BaseModel):
    from_point: str = Field(min_length=2, max_length=255)
    to_point: str = Field(min_length=2, max_length=255)
    price: int = Field(gt=0, le=1_000_000)


# --- Отклики -------------------------------------------------------------


class OfferIn(BaseModel):
    price: int = Field(gt=0, le=1_000_000)
    eta_minutes: int = Field(gt=0, le=240)


class OfferOut(OrmModel):
    id: int
    order_id: int
    driver_id: int
    driver_name: str = Field(validation_alias=AliasPath("driver", "username"))
    price: int
    eta_minutes: int
    status: OfferStatus
    created_at: datetime


class OrderOut(OrmModel):
    id: int
    passenger_id: int
    from_point: str
    to_point: str
    price: int
    status: OrderStatus
    assigned_offer_id: int | None = None
    created_at: datetime
    updated_at: datetime
    # Используется в ленте водителя: «я уже откликался на этот заказ».
    my_offer: OfferOut | None = None
