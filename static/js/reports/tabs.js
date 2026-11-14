/*
 * The reports' tab row (reports/_report_tabs.html). On a narrow screen the
 * row scrolls sideways (luxury.css), so the current report's tab is
 * brought into the middle of the view.
 */
export function centerActiveTab(strip) {
  const active = strip && strip.querySelector(".report-tab.active");
  if (!active || strip.scrollWidth <= strip.clientWidth) return false;
  const offset = active.getBoundingClientRect().left - strip.getBoundingClientRect().left + strip.scrollLeft;
  strip.scrollLeft = offset - (strip.clientWidth - active.offsetWidth) / 2;
  return true;
}

if (typeof document !== "undefined") {
  const active = document.querySelector(".report-tab.active");
  if (active) centerActiveTab(active.parentElement);
}
