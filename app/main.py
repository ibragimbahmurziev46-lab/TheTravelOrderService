"""Точка входа: FastAPI, роутеры, раздача статики."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import STATIC_DIR
from app.database import Base, engine
from app.models import Offer, Order, User  # noqa: F401  # регистрируем таблицы в metadata
from app.routers import auth, offers, orders, stream
from app.sse import bus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("taxi")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    bus.attach_loop(asyncio.get_running_loop())
    logger.info("Приложение запущено, таблицы БД готовы")
    yield


app = FastAPI(title="Такси: пассажиры и водители", version="1.0.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(orders.router)
app.include_router(offers.router)
app.include_router(stream.router)


from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Единый формат ошибок: {"detail": "..."} даже для ошибок валидации."""
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        message = str(error.get("msg", "некорректное значение"))
        messages.append(f"{location}: {message}" if location else message)
    return JSONResponse(
        status_code=422,
        content={"detail": "; ".join(messages) or "Некорректные данные"},
    )


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


# Монтируем статику последней, чтобы не перекрыть маршруты API.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
