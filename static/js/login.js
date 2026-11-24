/* Вход: POST /auth/login -> сохраняем токен и уходим в кабинет по роли. */
(function () {
  "use strict";

  const form = document.getElementById("login-form");
  const errorElement = document.getElementById("form-error");

  function setError(message) {
    if (!message) {
      errorElement.hidden = true;
      errorElement.textContent = "";
      return;
    }
    errorElement.hidden = false;
    errorElement.textContent = message;
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const username = form.elements.username.value.trim();
    const password = form.elements.password.value;

    setError("");
    button.disabled = true;
    try {
      const data = await apiFetch("/auth/login", {
        method: "POST",
        body: { username: username, password: password },
      });
      saveSession({ token: data.access_token, role: data.role, username: username });
      redirectToDashboard({ role: data.role });
    } catch (error) {
      setError(error.message);
    } finally {
      button.disabled = false;
    }
  });
})();
