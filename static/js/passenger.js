/* Кабинет пассажира: создание заказов, отклики, принятие и отмена + SSE. */
(function () {
  "use strict";

  const session = requireRole("passenger");
  if (!session) return;

  const ordersElement = document.getElementById("orders");
  const orderForm = document.getElementById("order-form");
  const refreshButton = document.getElementById("refresh");

  fillWhoami();
  bindLogout();

  function offerRow(order, offer) {
    const acceptButton = order.status === "open" && offer.status === "pending"
      ? '<button class="btn btn-primary btn-sm" type="button" data-accept="' + offer.id +
        '" data-order="' + order.id + '">Принять</button>'
      : "";
    return (
      '<div class="offer">' +
        "<div><strong>" + escapeHtml(offer.driver_name) + "</strong>" +
          '<span class="muted"> · ' + offer.price + " ₽ · подача " + offer.eta_minutes + " мин</span></div>" +
        '<div class="offer-right">' + statusBadge(offer.status) + acceptButton + "</div>" +
      "</div>"
    );
  }

  function orderCard(order) {
    const offersHtml = order.offers.length
      ? order.offers.map(function (offer) { return offerRow(order, offer); }).join("")
      : '<p class="muted">Откликов пока нет.</p>';
    const cancelButton = order.status === "open"
      ? '<button class="btn btn-danger btn-sm" type="button" data-cancel="' + order.id + '">Отменить заказ</button>'
      : "";
    const assignedHint = order.status === "assigned"
      ? '<p class="hint">Водитель назначен — ожидайте подачу машины.</p>'
      : "";
    return (
      '<article class="card">' +
        '<header class="card-head">' +
          "<h3>" + escapeHtml(order.from_point) + " → " + escapeHtml(order.to_point) + "</h3>" +
          statusBadge(order.status) +
        "</header>" +
        '<p class="muted">Цена: <strong>' + order.price + " ₽</strong> · создан " +
          fmtDate(order.created_at) + "</p>" +
        assignedHint +
        '<div class="offers"><h4>Отклики (' + order.offers.length + ")</h4>" + offersHtml + "</div>" +
        (cancelButton ? '<div class="card-actions">' + cancelButton + "</div>" : "") +
      "</article>"
    );
  }

  function render(orders) {
    if (!orders.length) {
      ordersElement.innerHTML = '<p class="muted">Заказов пока нет — создайте первый.</p>';
      return;
    }
    ordersElement.innerHTML = orders.map(orderCard).join("");
  }

  async function refreshOrders() {
    try {
      const list = await apiFetch("/orders/my");
      const orders = await Promise.all(list.map(async function (order) {
        let offers = [];
        if (order.status === "open" || order.status === "assigned") {
          try {
            offers = await apiFetch("/orders/" + order.id + "/offers");
          } catch (error) {
            offers = [];
          }
        }
        return Object.assign({}, order, { offers: offers });
      }));
      render(orders);
    } catch (error) {
      ordersElement.innerHTML = '<p class="error">' + escapeHtml(error.message) + "</p>";
    }
  }

  orderForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    const button = orderForm.querySelector('button[type="submit"]');
    const payload = {
      from_point: orderForm.elements.from_point.value.trim(),
      to_point: orderForm.elements.to_point.value.trim(),
      price: Number(orderForm.elements.price.value),
    };

    button.disabled = true;
    try {
      await apiFetch("/orders", { method: "POST", body: payload });
      orderForm.reset();
      showToast("Заказ создан, ждём отклики водителей", "success");
      await refreshOrders();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  // Делегирование кликов: принять отклик / отменить заказ.
  ordersElement.addEventListener("click", async function (event) {
    const acceptButton = event.target.closest("[data-accept]");
    const cancelButton = event.target.closest("[data-cancel]");

    try {
      if (acceptButton) {
        acceptButton.disabled = true;
        await apiFetch(
          "/orders/" + acceptButton.dataset.order + "/offers/" + acceptButton.dataset.accept + "/accept",
          { method: "POST" }
        );
        showToast("Отклик принят, водитель назначен", "success");
        await refreshOrders();
      } else if (cancelButton) {
        if (!window.confirm("Отменить заказ?")) return;
        cancelButton.disabled = true;
        await apiFetch("/orders/" + cancelButton.dataset.cancel + "/cancel", { method: "POST" });
        showToast("Заказ отменён", "success");
        await refreshOrders();
      }
    } catch (error) {
      showToast(error.message, "error");
      await refreshOrders();
    }
  });

  refreshButton.addEventListener("click", function () {
    refreshOrders();
  });

  const stream = openEventStream({
    onStatus: setStreamStatus,
    // SSE не гарантирует доставку пропущенных событий: после reconnect — REST-рефетч.
    onReconnect: function () {
      refreshOrders();
    },
    events: {
      new_offer: function (data) {
        showToast("Новый отклик по заказу #" + data.order_id + ": " + data.offer.price + " ₽", "info");
        refreshOrders();
      },
      order_completed: function (data) {
        showToast("Заказ #" + data.order_id + " завершён водителем", "success");
        refreshOrders();
      },
    },
  });

  window.addEventListener("beforeunload", function () {
    if (stream) stream.close();
  });

  refreshOrders();
})();
