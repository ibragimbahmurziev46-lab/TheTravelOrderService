/* Общие утилиты фронтенда: сессия, запросы к API, SSE, мелкие форматтеры. */
(function () {
  "use strict";

  const SESSION_KEY = "taxi.session";
  const API_BASE = "http://127.0.0.1:8000";
  const STATUS_LABELS = {
    open: "Открыт",
    assigned: "Назначен",
    cancelled: "Отменён",
    completed: "Завершён",
    pending: "Ожидает",
    accepted: "Принят",
    rejected: "Отклонён",
  };

  // sessionStorage, а не localStorage — иначе пассажир и водитель на одном origin перетирают друг друга.
  function getSession() {
    try {
      return JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
    } catch (error) {
      return null;
    }
  }

  function saveSession(session) {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  }

  function clearSession() {
    sessionStorage.removeItem(SESSION_KEY);
  }

  function dashboardUrl(role) {
    return role === "driver" ? "/driver.html" : "/passenger.html";
  }

  function redirectToDashboard(session) {
    const current = session || getSession();
    if (!current) {
      window.location.replace("/login.html");
      return;
    }
    window.location.replace(dashboardUrl(current.role));
  }

  // Проверка доступа к странице: нет токена -> логин, чужая роль -> свой кабинет.
  function requireRole(role) {
    const session = getSession();
    if (!session || !session.token) {
      window.location.replace("/login.html");
      return null;
    }
    if (role && session.role !== role) {
      window.location.replace(dashboardUrl(session.role));
      return null;
    }
    return session;
  }

  function errorMessage(detail, fallback) {
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) {
      return detail
        .map(function (item) { return item && item.msg ? item.msg : String(item); })
        .join("; ");
    }
    return fallback;
  }

  async function apiFetch(path, options) {
    const settings = options || {};
    const session = getSession();
    const headers = {};
    if (settings.body !== undefined && settings.body !== null) {
      headers["Content-Type"] = "application/json";
    }
    if (session && session.token) {
      headers.Authorization = "Bearer " + session.token;
    }

    const response = await fetch(API_BASE + path, {
      method: settings.method || "GET",
      headers: headers,
      body: settings.body === undefined || settings.body === null
        ? undefined
        : JSON.stringify(settings.body),
    });

    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (error) {
        data = text;
      }
    }

    if (response.status === 401) {
      clearSession();
      window.location.replace("/login.html");
      throw new Error("Сессия истекла, войдите заново");
    }
    if (!response.ok) {
      throw new Error(errorMessage(data && data.detail, "Ошибка " + response.status));
    }
    return data;
  }

  /**
   * Подписка на /stream?token=...
   * EventSource сам переподключается, но пропущенные события не доставляет —
   * поэтому после reconnect вызываем onReconnect (REST-рефетч состояния).
   */
  function openEventStream(handlers) {
    const options = handlers || {};
    const session = getSession();
    if (!session || !session.token) return null;

    let openedOnce = false;
    const source = new EventSource(API_BASE + "/stream?token=" + encodeURIComponent(session.token));

    source.onopen = function () {
      if (openedOnce && typeof options.onReconnect === "function") {
        options.onReconnect();
      }
      openedOnce = true;
      if (typeof options.onStatus === "function") options.onStatus("online");
    };
    source.onerror = function () {
      if (typeof options.onStatus === "function") options.onStatus("reconnecting");
    };

    Object.keys(options.events || {}).forEach(function (name) {
      source.addEventListener(name, function (event) {
        let payload = null;
        try {
          payload = JSON.parse(event.data);
        } catch (error) {
          payload = event.data;
        }
        options.events[name](payload);
      });
    });

    return source;
  }

  function escapeHtml(value) {
    return String(value === undefined || value === null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || status || "—";
  }

  function statusBadge(status) {
    const safe = String(status || "").replace(/[^a-z_]/gi, "");
    return '<span class="badge badge-' + safe + '">' + escapeHtml(statusLabel(status)) + "</span>";
  }

  let toastTimer = null;
  function showToast(message, kind) {
    const element = document.getElementById("toast");
    if (!element) return;
    element.hidden = false;
    element.textContent = message;
    element.className = "toast toast-" + (kind || "info");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      element.hidden = true;
    }, 4200);
  }

  function setStreamStatus(state) {
    const element = document.getElementById("stream-status");
    if (!element) return;
    element.dataset.state = state;
    element.textContent = state === "online" ? "● онлайн" : "… переподключение";
  }

  function fillWhoami() {
    const element = document.getElementById("whoami");
    const session = getSession();
    if (!element || !session) return;
    const role = session.role === "driver" ? "водитель" : "пассажир";
    element.textContent = session.username + " · " + role;
  }

  function bindLogout() {
    document.querySelectorAll('[data-action="logout"]').forEach(function (button) {
      button.addEventListener("click", function () {
        clearSession();
        window.location.replace("/login.html");
      });
    });
  }

  window.TaxiApi = {
    getSession: getSession,
    saveSession: saveSession,
    clearSession: clearSession,
    requireRole: requireRole,
    redirectToDashboard: redirectToDashboard,
    dashboardUrl: dashboardUrl,
    apiFetch: apiFetch,
    openEventStream: openEventStream,
    escapeHtml: escapeHtml,
    fmtDate: fmtDate,
    statusLabel: statusLabel,
    statusBadge: statusBadge,
    showToast: showToast,
    setStreamStatus: setStreamStatus,
    fillWhoami: fillWhoami,
    bindLogout: bindLogout,
  };

  // Удобные короткие псевдонимы для страничных скриптов.
  window.getSession = getSession;
  window.saveSession = saveSession;
  window.clearSession = clearSession;
  window.requireRole = requireRole;
  window.redirectToDashboard = redirectToDashboard;
  window.apiFetch = apiFetch;
  window.openEventStream = openEventStream;
  window.escapeHtml = escapeHtml;
  window.fmtDate = fmtDate;
  window.statusBadge = statusBadge;
  window.showToast = showToast;
  window.setStreamStatus = setStreamStatus;
  window.fillWhoami = fillWhoami;
  window.bindLogout = bindLogout;
})();