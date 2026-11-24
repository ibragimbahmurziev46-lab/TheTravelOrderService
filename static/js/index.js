/* Главная: если пользователь уже вошёл — ведём сразу в его кабинет. */
(function () {
  "use strict";

  const link = document.getElementById("dashboard-link");
  const session = getSession();
  if (!link || !session || !session.token) return;

  link.href = session.role === "driver" ? "/driver.html" : "/passenger.html";
  link.textContent = "Перейти в кабинет";
})();
