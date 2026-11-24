"""Зависимости авторизации: get_current_user, require_passenger, require_driver."""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Role, User
from app.security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)


def unauthorized(detail: str = "Требуется авторизация") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticate_token(token: str, db: Session) -> User:
    """Общая проверка токена для заголовка Authorization и для /stream?token=..."""
    payload = decode_token(token)
    if payload is None:
        raise unauthorized("Недействительный или истёкший токен")

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise unauthorized("Недействительный или истёкший токен")

    user = db.get(User, user_id)
    if user is None:
        raise unauthorized("Пользователь не найден")
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    return authenticate_token(credentials.credentials, db)


def require_passenger(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.passenger:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Действие доступно только пассажиру",
        )
    return user


def require_driver(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.driver:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Действие доступно только водителю",
        )
    return user
