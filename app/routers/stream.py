"""SSE-эндпоинт /stream?token=<jwt>."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import authenticate_token, unauthorized
from app.sse import create_stream_response

router = APIRouter(tags=["stream"])


@router.get("/stream")
async def stream(
    request: Request,
    token: str | None = Query(default=None, description="JWT access token"),
    db: Session = Depends(get_db),
):
    if not token:
        raise unauthorized("Не передан токен")
    try:
        user = authenticate_token(token, db)
    except HTTPException as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.detail,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await create_stream_response(request, user.id)
