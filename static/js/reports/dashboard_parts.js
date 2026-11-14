/*
 * The sales report's pure parts (reports/dashboard.html): the export
 * address with the page's filters, and the chart colours for light or
 * dark. Kept apart from the page wiring (dashboard.js) so node can test it.
 */
/* The export address for one report, with the page's filters. */
export function exportUrl(base, search, type) {
  const params = new URLSearchParams(search);
  params.set("type", type);
  return base + "?" + params.toString();
}

export function chartColours(dark) {
  return {
    text: dark ? "#888888" : "#737373",
    grid: dark ? "#262626" : "#eaeaea",
    payments: dark ? ["#ffffff", "#d4af37", "#555555", "#333333"] : ["#111111", "#c5a059", "#aaaaaa", "#e0e0e0"],
    ring: dark ? "#111111" : "#ffffff",
    line: dark ? "#d4af37" : "#111111",
    fill: dark ? ["rgba(212,175,55,0.4)", "rgba(212,175,55,0)"] : ["rgba(17,17,17,0.15)", "rgba(17,17,17,0)"],
    tip: dark ? ["#ffffff", "#111111"] : ["#111111", "#ffffff"],
  };
}
