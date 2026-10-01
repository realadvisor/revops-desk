document
  .querySelectorAll("[data-submit]")
  .forEach((element) =>
    element.addEventListener("change", () => element.form.requestSubmit()),
  );
document.querySelectorAll("[data-pending]").forEach((form) =>
  form.addEventListener("submit", () => {
    if (!form.checkValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = true;
      button.textContent = "Saving…";
    }
  }),
);
document.querySelectorAll("[data-confirm]").forEach((form) =>
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  }),
);
document.querySelectorAll("[data-copy], [data-copy-url]").forEach((button) =>
  button.addEventListener("click", async () => {
    const value = button.dataset.copy
      ? document.getElementById(button.dataset.copy).value
      : window.location.href;
    try {
      await navigator.clipboard.writeText(value);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select and copy the link";
    }
  }),
);
document.addEventListener("keydown", (event) => {
  if (
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)
  )
    return;
  if (event.key === "/" && document.getElementById("request-search")) {
    event.preventDefault();
    document.getElementById("request-search").focus();
  }
  if (event.key === "n" && document.querySelector(".new-button"))
    window.location.href = "/requests/new/";
});
