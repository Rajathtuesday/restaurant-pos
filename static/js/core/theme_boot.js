/*
 * Dark or light before the first paint, for a page without base.html (the
 * login page). A plain script in the head, not a module: a module runs
 * after the page is parsed, too late, and the page would flash light.
 * The same rule as base.html's boot script: localStorage "dark" = "true".
 */
(function () {
  var dark = false;
  try { dark = localStorage.getItem("dark") === "true"; } catch (e) { /* a private window */ }
  if (dark) document.documentElement.classList.add("dark");
})();
