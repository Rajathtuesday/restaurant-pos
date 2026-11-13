/*
 * Runs in the kitchen display's <head>, before the first paint (a plain
 * script, not a module: modules run only once the page is parsed, and a
 * cook who chose the light screen would see it dark for a moment on every
 * load). It makes the screen dark whatever the rest of Rasova is set to,
 * and light if this screen's own switch says so. The switch itself is
 * static/js/kitchen/light.js, which uses the same key.
 */
(function () {
  var root = document.documentElement;
  var light = false;
  try {
    light = localStorage.getItem("kitchen_light") === "true";
  } catch (noStorage) {
    /* a private window */
  }
  root.classList.add("dark");
  if (light) root.classList.add("kitchen-light");
})();
