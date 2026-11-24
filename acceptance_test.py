"""Приёмочный тест Task4: поднимает uvicorn на временной БД и проходит README-сценарий.

Запуск из папки Task4 интерпретатором из venv:
    venv\\Scripts\\python.exe acceptance_test.py

Скрипт сам находит свободный порт, поднимает сервер, открывает SSE-подписки
для пассажира и двух водителей и проверяет REST-коды вместе с событиями.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
TIMEOUT = 10.0

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> bool:
    if condition:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(name)
        print(f"[FAIL] {name}" + (f" :: {extra}" if extra else ""))
    return condition


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(base_url: str, process: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"сервер завершился с кодом {process.returncode}")
        try:
            if requests.get(f"{base_url}/health", timeout=1.5).status_code == 200:
                return
        except requests.RequestException:
            time.sleep(0.25)
    raise RuntimeError("сервер не поднялся за отведённое время")


class SSEClient(threading.Thread):
    """Читает /stream?token=... в отдельном потоке и складывает события в очередь."""

    def __init__(self, base_url: str, token: str) -> None:
        super().__init__(daemon=True)
        self.base_url = base_url
        self.token = token
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.ready = threading.Event()
        self.error: str | None = None
        self.status_code: int | None = None
        self._stop = threading.Event()
        self._response: requests.Response | None = None

    def run(self) -> None:
        try:
            self._response = requests.get(
                f"{self.base_url}/stream",
                params={"token": self.token},
                stream=True,
                timeout=(5, 60),
            )
            self.status_code = self._response.status_code
            if self._response.status_code != 200:
                self.error = self._response.text
                return
            self.ready.set()

            event_name: str | None = None
            data_lines: list[str] = []
            for raw in self._response.iter_lines(decode_unicode=True):
                if self._stop.is_set():
                    break
                if raw is None:
                    continue
                line = raw.rstrip("\r")
                if line == "":
                    if event_name is not None or data_lines:
                        self.events.put((event_name or "message", "\n".join(data_lines)))
                    event_name, data_lines = None, []
                    continue
                if line.startswith(":"):
                    continue  # heartbeat ": ping"
                field, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if field == "event":
                    event_name = value
                elif field == "data":
                    data_lines.append(value)
        except Exception as exc:  # noqa: BLE001 - тест должен показать причину
            self.error = repr(exc)
        finally:
            self.ready.set()

    def wait_for(self, name: str, predicate=None, timeout: float = TIMEOUT):
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise AssertionError(f"событие {name!r} не пришло за {timeout} c")
            try:
                event, data = self.events.get(timeout=remaining)
            except queue.Empty:
                continue
            if event != name:
                continue
            payload = json.loads(data) if data else None
            if predicate is None or predicate(payload):
                return payload

    def close(self) -> None:
        self._stop.set()
        if self._response is not None:
            self._response.close()


def open_stream(base_url: str, token: str, label: str) -> SSEClient:
    client = SSEClient(base_url, token)
    client.start()
    if not client.ready.wait(TIMEOUT):
        raise AssertionError(f"SSE {label}: соединение не открылось")
    if client.status_code != 200:
        raise AssertionError(f"SSE {label}: код {client.status_code} ({client.error})")
    # Даём серверу зарегистрировать подписку до первых публикаций.
    time.sleep(0.3)
    return client


def start_server(base_url: str, db_path: Path, log_path: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["DB_URL"] = f"sqlite:///{db_path.as_posix()}"
    env["SSE_HEARTBEAT_SECONDS"] = "2"
    env["SECRET_KEY"] = "acceptance-test-secret"
    env.setdefault("PYTHONIOENCODING", "utf-8")

    log_file = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - живёт до конца теста
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(base_url.rsplit(":", 1)[1]),
        ],
        cwd=str(BASE_DIR),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_for_health(base_url, process)
    except Exception:
        process.terminate()
        log_file.close()
        raise
    return process

def auth(token: str | None) -> dict[str, str] | None:
    return {"Authorization": f"Bearer {token}"} if token else None


def run_scenario(base_url: str, streams: list[SSEClient]) -> None:
    def post(path: str, token: str | None = None, body=None) -> requests.Response:
        return requests.post(base_url + path, json=body, headers=auth(token), timeout=TIMEOUT)

    def get(path: str, token: str | None = None) -> requests.Response:
        return requests.get(base_url + path, headers=auth(token), timeout=TIMEOUT)

    # --- 0. Статика -------------------------------------------------------
    for path in (
        "/",
        "/index.html",
        "/login.html",
        "/register.html",
        "/passenger.html",
        "/driver.html",
        "/css/style.css",
        "/js/api.js",
        "/js/passenger.js",
        "/js/driver.js",
    ):
        response = get(path)
        check(f"GET {path} -> 200", response.status_code == 200, str(response.status_code))
    check("GET / отдаёт index.html", "Такси" in get("/").text)

    # --- 1. Регистрация и логин ------------------------------------------
    accounts = {
        "passenger": ("passenger1", "passenger"),
        "passenger2": ("passenger2", "passenger"),
        "driver1": ("driver1", "driver"),
        "driver2": ("driver2", "driver"),
    }
    for username, role in accounts.values():
        response = post(
            "/auth/register",
            body={"username": username, "password": "secret123", "role": role},
        )
        check(f"POST /auth/register {username} -> 201", response.status_code == 201, response.text)

    response = post(
        "/auth/register",
        body={"username": "passenger1", "password": "secret123", "role": "passenger"},
    )
    check("повторная регистрация -> 400", response.status_code == 400, response.text)

    response = post(
        "/auth/register",
        body={"username": "x", "password": "123", "role": "passenger"},
    )
    check(
        "невалидные данные регистрации -> 422 + detail",
        response.status_code == 422 and "detail" in response.json(),
        response.text,
    )

    tokens: dict[str, str] = {}
    for key, (username, role) in accounts.items():
        response = post("/auth/login", body={"username": username, "password": "secret123"})
        payload = response.json() if response.status_code == 200 else {}
        check(
            f"POST /auth/login {username} -> 200 + токен",
            response.status_code == 200 and bool(payload.get("access_token")),
            response.text,
        )
        check(f"login {username}: role={role}", payload.get("role") == role, str(payload))
        tokens[key] = payload.get("access_token", "")


    response = post("/auth/login", body={"username": "passenger1", "password": "wrong-password"})
    check("неверный пароль -> 401", response.status_code == 401, response.text)

    # --- 2. 401 / 403 до основного сценария ------------------------------
    check("GET /orders/my без токена -> 401", get("/orders/my").status_code == 401)
    check(
        "GET /orders/my с битым токеном -> 401",
        get("/orders/my", "broken.token.value").status_code == 401,
    )
    check("GET /stream без токена -> 401", get("/stream").status_code == 401)
    check(
        "GET /stream с битым токеном -> 401",
        get("/stream?token=broken.token.value").status_code == 401,
    )
    check(
        "водитель создаёт заказ -> 403",
        post("/orders", tokens["driver1"], {"from_point": "A", "to_point": "B", "price": 100}).status_code == 403,
    )
    check(
        "пассажир смотрит ленту водителя -> 403",
        get("/orders/feed", tokens["passenger"]).status_code == 403,
    )

    # --- 3. Заказы и SSE new_order --------------------------------------
    driver1_stream = open_stream(base_url, tokens["driver1"], "driver1")
    streams.append(driver1_stream)
    driver2_stream = open_stream(base_url, tokens["driver2"], "driver2")
    streams.append(driver2_stream)
    passenger_stream = open_stream(base_url, tokens["passenger"], "passenger")
    streams.append(passenger_stream)

    response = post(
        "/orders",
        tokens["passenger"],
        {"from_point": "Аэропорт", "to_point": "Центр", "price": 500},
    )
    check("POST /orders пассажиром -> 201", response.status_code == 201, response.text)
    order = response.json()
    order_id = order["id"]
    check("новый заказ в статусе open", order.get("status") == "open", str(order))

    for label, stream in (("driver1", driver1_stream), ("driver2", driver2_stream)):
        event = stream.wait_for("new_order", lambda payload: payload.get("id") == order_id)
        check(
            f"SSE {label}: new_order {{id, from_point, to_point, price}}",
            {"id", "from_point", "to_point", "price"} <= set(event),
            str(event),
        )

    # Второй заказ нужен ниже для проверки отмены и её SSE.
    response = post(
        "/orders",
        tokens["passenger"],
        {"from_point": "Вокзал", "to_point": "Парк", "price": 300},
    )
    check("POST /orders (второй заказ) -> 201", response.status_code == 201, response.text)
    order2_id = response.json()["id"]

    # --- 4. Отклики ------------------------------------------------------
    response = post(
        "/orders/" + str(order_id) + "/offers",
        tokens["passenger"],
        {"price": 450, "eta_minutes": 5},
    )
    check("пассажир отправляет отклик -> 403", response.status_code == 403, response.text)

    response = post(
        "/orders/" + str(order_id) + "/offers",
        tokens["driver1"],
        {"price": 450, "eta_minutes": 5},
    )
    check("POST /orders/{id}/offers -> 201", response.status_code == 201, response.text)
    offer1 = response.json()
    check("отклик содержит driver_name", offer1.get("driver_name") == "driver1", str(offer1))

    response = post(
        "/orders/" + str(order_id) + "/offers",
        tokens["driver1"],
        {"price": 400, "eta_minutes": 4},
    )
    check("повторный отклик того же водителя -> 409", response.status_code == 409, response.text)

    response = post(
        "/orders/" + str(order_id) + "/offers",
        tokens["driver2"],
        {"price": 600, "eta_minutes": 8},
    )
    check("POST /orders/{id}/offers вторым водителем -> 201", response.status_code == 201, response.text)
    offer2 = response.json()

    event = passenger_stream.wait_for(
        "new_offer",
        lambda payload: payload.get("order_id") == order_id
        and payload.get("offer", {}).get("id") == offer2["id"],
    )
    check(
        "SSE passenger: new_offer с полями offer",
        {"id", "driver_id", "driver_name", "price", "eta_minutes"} <= set(event["offer"]),
        str(event),
    )

    # --- 5. Ленты до принятия и доступ к чужому заказу -------------------
    response = get("/orders/feed", tokens["driver1"])
    feed = response.json() if response.status_code == 200 else []
    check("GET /orders/feed -> 200", response.status_code == 200, response.text)
    feed_item = next((item for item in feed if item["id"] == order_id), None)
    check("лента водителя содержит заказ", feed_item is not None, str(feed))
    check(
        "лента помечает собственный отклик (my_offer)",
        bool(feed_item and feed_item.get("my_offer") and feed_item["my_offer"]["price"] == 450),
        str(feed_item),
    )

    response = get("/orders/" + str(order_id) + "/offers", tokens["passenger"])
    check("GET /orders/{id}/offers владельцем -> 200 + 2 отклика", response.status_code == 200 and len(response.json()) == 2, response.text)
    check(
        "чужой заказ: отклики -> 403",
        get("/orders/" + str(order_id) + "/offers", tokens["passenger2"]).status_code == 403,
    )
    check(
        "чужой заказ: отмена -> 403",
        post("/orders/" + str(order_id) + "/cancel", tokens["passenger2"]).status_code == 403,
    )
    check(
        "несуществующий заказ -> 404",
        get("/orders/999999/offers", tokens["passenger"]).status_code == 404,
    )
    check(
        "водитель смотрит свои заказы пассажира -> 403",
        get("/orders/my", tokens["driver1"]).status_code == 403,
    )
    check(
        "пассажир смотрит назначенные водителя -> 403",
        get("/orders/assigned", tokens["passenger"]).status_code == 403,
    )

    # --- 6. Принятие отклика ---------------------------------------------
    response = post(
        "/orders/" + str(order_id) + "/offers/" + str(offer1["id"]) + "/accept",
        tokens["passenger"],
    )
    check("accept отклика -> 200", response.status_code == 200, response.text)
    accepted_order = response.json()
    check(
        "после accept: status=assigned, assigned_offer_id",
        accepted_order.get("status") == "assigned"
        and accepted_order.get("assigned_offer_id") == offer1["id"],
        str(accepted_order),
    )

    response = post(
        "/orders/" + str(order_id) + "/offers/" + str(offer2["id"]) + "/accept",
        tokens["passenger"],
    )
    check("второй accept -> 409", response.status_code == 409, response.text)

    decision = driver1_stream.wait_for(
        "offer_decision",
        lambda payload: payload.get("order_id") == order_id and payload.get("accepted") is True,
    )
    check(
        "SSE driver1: offer_decision{accepted: true}",
        decision.get("offer_id") == offer1["id"],
        str(decision),
    )
    decision = driver2_stream.wait_for(
        "offer_decision",
        lambda payload: payload.get("order_id") == order_id,
    )
    check(
        "SSE driver2: offer_decision{accepted: false}",
        decision.get("accepted") is False and decision.get("offer_id") == offer2["id"],
        str(decision),
    )
    for label, stream in (("driver1", driver1_stream), ("driver2", driver2_stream)):
        closed = stream.wait_for("order_closed", lambda payload: payload.get("order_id") == order_id)
        check(f"SSE {label}: order_closed{{reason: 'assigned'}}", closed.get("reason") == "assigned", str(closed))

    # --- 7. Завершение поездки -------------------------------------------
    response = post("/orders/" + str(order_id) + "/complete", tokens["driver2"])
    check("complete чужим водителем -> 403", response.status_code == 403, response.text)

    response = post("/orders/" + str(order_id) + "/complete", tokens["driver1"])
    check("POST /orders/{id}/complete -> 200", response.status_code == 200, response.text)
    check("после complete: status=completed", response.json().get("status") == "completed", response.text)

    event = passenger_stream.wait_for("order_completed", lambda payload: payload.get("order_id") == order_id)
    check("SSE passenger: order_completed {order_id}", event.get("order_id") == order_id, str(event))

    response = post("/orders/" + str(order_id) + "/complete", tokens["driver1"])
    check("повторный complete -> 409", response.status_code == 409, response.text)

    assigned = get("/orders/assigned", tokens["driver1"])
    check("GET /orders/assigned -> 200", assigned.status_code == 200, assigned.text)
    check(
        "assigned водителя содержит завершённый заказ",
        any(item["id"] == order_id and item["status"] == "completed" for item in assigned.json()),
        assigned.text,
    )
    check(
        "assigned второго водителя не содержит чужой заказ",
        all(item["id"] != order_id for item in get("/orders/assigned", tokens["driver2"]).json()),
    )
    check(
        "GET /orders/my содержит завершённый заказ",
        any(item["id"] == order_id and item["status"] == "completed" for item in get("/orders/my", tokens["passenger"]).json()),
    )
    check(
        "закрытый заказ исчез из ленты водителей",
        all(item["id"] != order_id for item in get("/orders/feed", tokens["driver2"]).json()),
    )

    # --- 8. Отмена заказа и её SSE ---------------------------------------
    response = post(
        "/orders/" + str(order_id) + "/offers",
        tokens["driver1"],
        {"price": 400, "eta_minutes": 5},
    )
    check("отклик на завершённый заказ -> 409", response.status_code == 409, response.text)

    response = post(
        "/orders/" + str(order2_id) + "/offers",
        tokens["driver2"],
        {"price": 320, "eta_minutes": 3},
    )
    check("отклик на второй заказ -> 201", response.status_code == 201, response.text)
    offer3 = response.json()

    response = post("/orders/" + str(order2_id) + "/cancel", tokens["passenger"])
    check("POST /orders/{id}/cancel -> 200", response.status_code == 200, response.text)
    check("после cancel: status=cancelled", response.json().get("status") == "cancelled", response.text)

    decision = driver2_stream.wait_for("offer_decision", lambda payload: payload.get("order_id") == order2_id)
    check(
        "SSE driver2: offer_decision{accepted: false} при отмене",
        decision.get("accepted") is False and decision.get("offer_id") == offer3["id"],
        str(decision),
    )
    closed = driver2_stream.wait_for("order_closed", lambda payload: payload.get("order_id") == order2_id)
    check("SSE driver2: order_closed{reason: 'cancelled'}", closed.get("reason") == "cancelled", str(closed))

    response = post("/orders/" + str(order2_id) + "/cancel", tokens["passenger"])
    check("повторный cancel -> 409", response.status_code == 409, response.text)
    response = post(
        "/orders/" + str(order2_id) + "/offers/" + str(offer3["id"]) + "/accept",
        tokens["passenger"],
    )
    check("accept отменённого заказа -> 409", response.status_code == 409, response.text)
    response = post(
        "/orders/" + str(order2_id) + "/offers",
        tokens["driver1"],
        {"price": 300, "eta_minutes": 2},
    )
    check("отклик на отменённый заказ -> 409", response.status_code == 409, response.text)
    response = post("/orders/" + str(order2_id) + "/complete", tokens["driver2"])
    check("complete отменённого заказа -> 403/409", response.status_code in (403, 409), response.text)


def main() -> int:
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    tmp_dir = Path(tempfile.mkdtemp(prefix="taxi-acceptance-"))
    db_path = tmp_dir / "acceptance.db"
    log_path = tmp_dir / "server.log"

    print(f"Сервер: {base_url}\nБД: {db_path}\n")
    process = start_server(base_url, db_path, log_path)
    streams: list[SSEClient] = []
    try:
        run_scenario(base_url, streams)
    except Exception as exc:  # noqa: BLE001 - показываем причину падения сценария
        check("сценарий выполнен без исключений", False, repr(exc))
    finally:
        for stream in streams:
            stream.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    print()
    if FAILED:
        print(f"ИТОГ: провалено {len(FAILED)} из {len(PASSED) + len(FAILED)} проверок")
        for name in FAILED:
            print(" - " + name)
        print(f"\nЛог сервера: {log_path}\n")
        print("\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))
        return 1

    print(f"ИТОГ: все {len(PASSED)} проверок прошли")
    print(f"Лог сервера: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
