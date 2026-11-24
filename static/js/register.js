/* Регистрация: POST /auth/register, затем сразу логинимся и идём в кабинет. */
(function () {
  "use strict";

  const form = document.getElementById("register-form");
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
    const role = form.elements.role.value;

    setError("");
    button.disabled = true;
    try {
      await apiFetch("/auth/register", {
        method: "POST",
        body: { username: username, password: password, role: role },
      });
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
