<<<<<<< HEAD
Структура проекта
text

app/
  main.py            # создание FastAPI, подключение роутеров, StaticFiles
  config.py          # SECRET_KEY, ALGORITHM (HS256), ACCESS_TOKEN_EXPIRE_MINUTES, DB_URL
  database.py        # engine, SessionLocal, Base, get_db()
  models.py          # SQLAlchemy-модели: User, Order, Offer
  schemas.py         # Pydantic-схемы запросов/ответов
  security.py        # bcrypt-хеш, verify, create_access_token, decode
  deps.py            # get_current_user, require_passenger, require_driver
  sse.py             # реестр подключений + publish() + StreamingResponse
  routers/
    auth.py          # /auth/register, /auth/login
    orders.py        # /orders ...
    offers.py        # /orders/{id}/offers, /offers/{id}/accept ...
    stream.py        # /stream
static/              # index.html, login.html, register.html, passenger.html, driver.html, css/, js/

Раздача фронта: FastAPI монтирует StaticFiles(directory="static", html=True) на /. CORS не нужен, если фронт ходит на тот же origin.
Модели (SQLAlchemy) и схемы (Pydantic)

Промт должен задать явные поля и enum-статусы, чтобы агент не гадал.

users

    id PK

    username unique, not null

    password_hash not null

    role enum: passenger | driver

orders

    id PK

    passenger_id FK → users.id

    from_point, to_point (str)

    price (int, > 0)

    status enum: open | assigned | cancelled | completed

    assigned_offer_id FK → offers.id, nullable

    created_at, updated_at (UTC)

offers

    id PK

    order_id FK → orders.id

    driver_id FK → users.id

    price (int, > 0)

    eta_minutes (int, > 0) — «время подачи»

    status enum: pending | accepted | rejected

    created_at

    unique constraint (order_id, driver_id) — гарантия «один водитель = один отклик»

Pydantic-схемы (schemas.py):

    RegisterIn{username, password, role}, LoginIn{username, password}

    TokenOut{access_token, token_type="bearer", role}

    OrderIn{from_point, to_point, price}, OrderOut{...}

    OfferIn{price, eta_minutes}, OfferOut{...}

    UserOut{id, username, role}

Правило для агента: модели SQLAlchemy и схемы Pydantic — разные классы, Pydantic никогда не трогает БД напрямую. Валидация полей (длина, >0, min_length) — на уровне схем через Field(...).
Авторизация

    Пароль: bcrypt (библиотека passlib[bcrypt] или напрямую bcrypt). В БД только хеш.

    JWT: python-jose или pyjwt, HS256, payload {sub: user_id, role, exp}. Время жизни — 60–120 минут, зафиксировать в config.

    get_current_user — зависимость: читает Authorization: Bearer <token>, декодирует, тянет юзера из БД; нет/битый/просроченный токен → 401.

    require_passenger / require_driver — обёртки над get_current_user, проверяют роль → 403 при несовпадении.

    Имя пользователя никогда не берётся из тела/квери — только из токена.

REST-эндпоинты (контракт)
Метод	Путь	Кто	Тело	Ответ	Ошибки
POST	/auth/register	all	RegisterIn	201 + UserOut	400 duplicate username
POST	/auth/login	all	LoginIn	200 + TokenOut	401 wrong creds
POST	/orders	passenger	OrderIn	201 + OrderOut	403 не пассажир
GET	/orders/my	passenger	—	200 [OrderOut]	403
GET	/orders/{id}/offers	passenger (свой заказ)	—	200 [OfferOut]	403 чужой заказ, 404
POST	/orders/{id}/offers/{offer_id}/accept	passenger (свой заказ)	—	200 OrderOut	403/404, 409 если заказ уже не open
POST	/orders/{id}/cancel	passenger (свой заказ)	—	200 OrderOut	403/404/409
GET	/orders/feed	driver	—	200 [OrderOut] (только status=open, помечая my_offer если уже откликался)	403
POST	/orders/{id}/offers	driver	OfferIn	201 + OfferOut	403, 404, 409 duplicate, 409 заказ не open
GET	/orders/assigned	driver	—	200 [OrderOut] где assigned_offer_id → моё	403
POST	/orders/{id}/complete	driver (принятый)	—	200 OrderOut	403 если offer не мой, 409 если не assigned

Коды ошибок — через HTTPException(detail=...), единый формат {"detail": "..."}.
Бизнес-правила и переходы состояний

Агенту надо явно сказать:

    Создать заказ — только passenger, статус open.

    Отклик — только driver, только на open-заказ, только один раз (unique constraint ловит 409).

    Принятие отклика — только владелец заказа, только если заказ open. В одной транзакции:

        order.status = assigned, order.assigned_offer_id = offer.id

        принятый offer.status = accepted

        все остальные pending-отклики этого заказа → rejected

        публикация SSE: принятому — offer_decision{accepted:true}, остальным — offer_decision{accepted:false}, всем водителям — order_closed{reason:"assigned"}

    Отмена заказа — только пассажир, только пока open (или до accept). Статус cancelled, все pending-отклики → rejected, SSE order_closed{reason:"cancelled"} всем водителям + offer_decision{accepted:false} каждому откликнувшемуся.

    Завершение — только водитель, чей отклик accepted, только из assigned → completed. SSE пассажиру order_completed.

    Гонки: accept/cancel/complete оборачиваем в транзакцию с проверкой текущего статуса (в идеале — SELECT ... FOR UPDATE или UPDATE ... WHERE status='open' и смотрим rowcount). Двойной accept должен дать 409, а не два «accepted».

SSE — контракт

    Реестр: dict[user_id, set[asyncio.Queue]] под asyncio.Lock. Поддерживает несколько вкладок на юзера.

    publish(user_id, event, data) — кладёт (event, data) во все очереди юзера, неблокирующий.

    Эндпоинт GET /stream?token=<jwt>:

        валидирует токен (тот же decode, что и в get_current_user), иначе 401;

        StreamingResponse(media_type="text/event-stream"), заголовки Cache-Control: no-cache, X-Accel-Buffering: no, Connection: keep-alive;

        формат кадра: event: <name>\ndata: <json>\n\n;

        heartbeat-комментарий : ping\n\n раз в 15–20 сек — иначе прокси/браузер рвёт соединение;

        при отключении клиента — try/finally с удалением очереди из реестра.

    Имена событий и payload — зафиксировать:

        new_order → {id, from_point, to_point, price} (всем водителям)

        order_closed → {order_id, reason: "assigned" | "cancelled"} (всем водителям)

        new_offer → {order_id, offer: {id, driver_id, driver_name, price, eta_minutes}} (пассажиру-владельцу)

        offer_decision → {order_id, offer_id, accepted: bool} (конкретному водителю)

        order_completed → {order_id} (пассажиру)

    На фронте после reconnect EventSource — обязательный REST-рефетч текущего состояния (SSE не гарантирует доставку пропущенных событий). Это правило прописать явно, иначе будут «залипшие» экраны.

Технические решения, которые надо зафиксировать

    Sync SQLAlchemy (Session в def-эндпоинтах) — проще и надёжнее для SQLite, чем async. Async только для SSE-стрима.

    check_same_thread=False у SQLite-движка + SessionLocal на запрос через get_db().

    Base.metadata.create_all(bind=engine) на старте (миграции не нужны для учебного).

    Единый datetime.now(timezone.utc) для всех created_at.

    Логирование не обязательно, но пусть будет print/logging.info на publish — удобно дебажить SSE.

Самопроверка (приёмка)

Агент после генерации обязан прогнать сценарий и показать результат:

    POST /auth/register passenger + driver → 201.

    POST /auth/login обоим → токены.

    Passenger POST /orders → 201.

    Driver открывает /stream?token=... → приходит new_order.

    Driver POST /orders/{id}/offers → 201; повторный → 409.

    Passenger /stream?token=... → приходит new_offer.

    Passenger POST .../accept → 200; второй accept → 409.

    У принятого водителя SSE offer_decision{accepted:true}, у второго — accepted:false, у всех order_closed.

    Driver POST /orders/{id}/complete → 200, у пассажира order_completed.

    Проверки 401/403: без токена — 401; пассажир делает offer — 403; водитель создаёт заказ — 403; чужой заказ — 403.

Хранение токена

Токен хранится в sessionStorage — у каждой вкладки своя сессия; это позволяет параллельно держать кабинеты пассажира и водителя на одном origin.
=======
# TheTravelOrderService
Core service responsible for creating, managing, and processing travel orders and bookings.
>>>>>>> 05793200602371f7b0c041045c5260c38bd703c2
