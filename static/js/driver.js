/* Кабинет водителя: лента открытых заказов, отклики, завершение поездок + SSE. */
(function () {
  "use strict";

  const session = requireRole("driver");
  if (!session) return;

  const feedElement = document.getElementById("feed");
  const assignedElement = document.getElementById("assigned");
  const refreshButton = document.getElementById("refresh");

  fillWhoami();
  bindLogout();

  function feedCard(order) {
    let action;
    if (order.my_offer) {
      action =
        '<div class="hint">Ваш отклик: <strong>' + order.my_offer.price + " ₽</strong>, подача " +
        order.my_offer.eta_minutes + " мин · " + statusBadge(order.my_offer.status) + "</div>";
    } else {
      action =
        '<form class="offer-form" data-offer-form="' + order.id + '">' +
          '<label class="field field-inline"><span>Цена, ₽</span>' +
            '<input name="price" type="number" min="1" step="1" value="' + order.price + '" required /></label>' +
          '<label class="field field-inline"><span>Подача, мин</span>' +
            '<input name="eta" type="number" min="1" max="240" step="1" value="5" required /></label>' +
          '<button class="btn btn-primary btn-sm" type="submit">Откликнуться</button>' +
        "</form>";
    }
    return (
      '<article class="card">' +
        '<header class="card-head">' +
          "<h3>" + escapeHtml(order.from_point) + " → " + escapeHtml(order.to_point) + "</h3>" +
          statusBadge(order.status) +
        "</header>" +
        '<p class="muted">Цена пассажира: <strong>' + order.price + " ₽</strong> · создан " +
          fmtDate(order.created_at) + "</p>" +
        action +
      "</article>"
    );
  }

  function assignedCard(order) {
    const completeButton = order.status === "assigned"
      ? '<button class="btn btn-primary btn-sm" type="button" data-complete="' + order.id + '">Завершить поездку</button>'
      : "";
    return (
      '<article class="card">' +
        '<header class="card-head">' +
          "<h3>" + escapeHtml(order.from_point) + " → " + escapeHtml(order.to_point) + "</h3>" +
          statusBadge(order.status) +
        "</header>" +
        '<p class="muted">Цена: <strong>' + order.price + " ₽</strong> · обновлён " +
          fmtDate(order.updated_at) + "</p>" +
        (completeButton ? '<div class="card-actions">' + completeButton + "</div>" : "") +
      "</article>"
    );
  }

  async function refreshFeed() {
    try {
      const orders = await apiFetch("/orders/feed");
      feedElement.innerHTML = orders.length
        ? orders.map(feedCard).join("")
        : '<p class="muted">Открытых заказов пока нет. Новые придут по SSE.</p>';
    } catch (error) {
      feedElement.innerHTML = '<p class="error">' + escapeHtml(error.message) + "</p>";
    }
  }

  async function refreshAssigned() {
    try {
      const orders = await apiFetch("/orders/assigned");
      assignedElement.innerHTML = orders.length
        ? orders.map(assignedCard).join("")
        : '<p class="muted">Назначенных поездок пока нет.</p>';
    } catch (error) {
      assignedElement.innerHTML = '<p class="error">' + escapeHtml(error.message) + "</p>";
    }
  }

  feedElement.addEventListener("submit", async function (event) {
    const form = event.target.closest("form[data-offer-form]");
    if (!form) return;
    event.preventDefault();

    const button = form.querySelector('button[type="submit"]');
    const orderId = form.dataset.offerForm;
    const payload = {
      price: Number(form.elements.price.value),
      eta_minutes: Number(form.elements.eta.value),
    };

    button.disabled = true;
    try {
      await apiFetch("/orders/" + orderId + "/offers", { method: "POST", body: payload });
      showToast("Отклик отправлен пассажиру", "success");
      await Promise.all([refreshFeed(), refreshAssigned()]);
    } catch (error) {
      showToast(error.message, "error");
      await refreshFeed();
    } finally {
      button.disabled = false;
    }
  });

  assignedElement.addEventListener("click", async function (event) {
    const completeButton = event.target.closest("[data-complete]");
    if (!completeButton) return;

    completeButton.disabled = true;
    try {
      await apiFetch("/orders/" + completeButton.dataset.complete + "/complete", { method: "POST" });
      showToast("Поездка завершена", "success");
      await Promise.all([refreshFeed(), refreshAssigned()]);
    } catch (error) {
      showToast(error.message, "error");
      await refreshAssigned();
    }
  });

  refreshButton.addEventListener("click", function () {
    refreshFeed();
    refreshAssigned();
  });

  const stream = openEventStream({
    onStatus: setStreamStatus,
    // После reconnect обязательно перечитываем состояние через REST.
    onReconnect: function () {
      refreshFeed();
      refreshAssigned();
    },
    events: {
      new_order: function () {
        showToast("Новый заказ в ленте", "info");
        refreshFeed();
      },
      order_closed: function (data) {
        const message = data.reason === "cancelled"
          ? "Заказ #" + data.order_id + " отменён пассажиром"
          : "Заказ #" + data.order_id + " назначен другому водителю";
        showToast(message, "info");
        refreshFeed();
        refreshAssigned();
      },
      offer_decision: function (data) {
        if (data.accepted) {
          showToast("Ваш отклик по заказу #" + data.order_id + " принят!", "success");
        } else {
          showToast("Отклик по заказу #" + data.order_id + " отклонён", "error");
        }
        refreshFeed();
        refreshAssigned();
      },
    },
  });

  window.addEventListener("beforeunload", function () {
    if (stream) stream.close();
  });

  refreshFeed();
  refreshAssigned();
})();

