/*
 * Runs in the QR menu's <head>, before the first paint (a plain script, not
 * a module: modules run only once the page is parsed, and a guest who chose
 * dark would see the page light for a moment on every load). The choice is
 * kept under "dark", the same key as the rest of Rasova on this device.
 */
(function () {
  try {
    if (localStorage.getItem("dark") === "true") document.documentElement.classList.add("dark");
  } catch (noStorage) {
    /* a private window: light */
  }
})();
