"""Конфигурация приложения.

Все значения можно переопределить переменными окружения — это удобно
для запуска приёмочных тестов на отдельной базе.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-me-before-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120"))

# SSE heartbeat: комментарий ": ping" раз в N секунд, иначе прокси/браузер рвёт соединение.
HEARTBEAT_SECONDS = int(os.getenv("SSE_HEARTBEAT_SECONDS", "15"))

DB_URL = os.getenv("DB_URL", f"sqlite:///{(BASE_DIR / 'taxi.db').as_posix()}")

STATIC_DIR = BASE_DIR / "static"
