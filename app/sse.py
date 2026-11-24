"""SSE: реестр подключений, publish() и StreamingResponse.

Эндпоинты синхронные (`def`) и выполняются в threadpool, а реестр очередей
живёт в event loop. Поэтому publish() потокобезопасно планирует доставку через
loop.call_soon_threadsafe, а сами очереди трогаются только из loop-потока.
"""
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import HEARTBEAT_SECONDS
from app.models import Role, User

logger = logging.getLogger("taxi.sse")


class EventBus:
    """dict[user_id, set[asyncio.Queue]] под asyncio.Lock (несколько вкладок на юзера)."""

    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Запоминаем loop приложения (вызывается в lifespan)."""
        self._loop = loop

    async def subscribe(self, user_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers.setdefault(user_id, set()).add(queue)
            connections = len(self._subscribers[user_id])
        logger.info("SSE subscribe user=%s connections=%s", user_id, connections)
        return queue

    async def unsubscribe(self, user_id: int, queue: asyncio.Queue) -> None:
        async with self._lock:
            queues = self._subscribers.get(user_id)
            if queues is None:
                return
            queues.discard(queue)
            if not queues:
                self._subscribers.pop(user_id, None)
        logger.info("SSE unsubscribe user=%s", user_id)

    def publish(self, user_id: int, event: str, data: dict[str, Any]) -> None:
        """Неблокирующе кладёт событие во все очереди пользователя."""
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning("SSE publish skipped (loop is not running): %s -> user=%s", event, user_id)
            return
        loop.call_soon_threadsafe(self._deliver, user_id, event, data)

    def _deliver(self, user_id: int, event: str, data: dict[str, Any]) -> None:
        """Выполняется в loop-потоке, поэтому put_nowait безопасен."""
        queues = self._subscribers.get(user_id)
        if not queues:
            logger.debug("SSE no subscribers: %s -> user=%s", event, user_id)
            return
        for queue in list(queues):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                logger.warning("SSE queue is full, event dropped: %s -> user=%s", event, user_id)
        logger.info("SSE publish event=%s user=%s payload=%s", event, user_id, data)


bus = EventBus()


def publish_to_drivers(db: Session, event: str, data: dict[str, Any]) -> None:
    """Рассылка всем водителям (new_order / order_closed)."""
    driver_ids = db.scalars(select(User.id).where(User.role == Role.driver)).all()
    for driver_id in driver_ids:
        bus.publish(driver_id, event, data)


async def create_stream_response(request: Request, user_id: int) -> StreamingResponse:
    queue = await bus.subscribe(user_id)

    async def event_generator() -> AsyncIterator[str]:
        try:
            # Первый кадр-комментарий: браузер сразу считает соединение открытым.
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                payload = json.dumps(data, ensure_ascii=False)
                yield f"event: {event}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            await bus.unsubscribe(user_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
