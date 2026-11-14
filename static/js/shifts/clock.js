/*
 * The live clock on the shifts page (shifts/shift_dashboard.html), once a
 * second. Clock in, clock out and tips are post buttons and forms
 * (core/post_actions.js), so this is all the page's own script.
 */
export function startClock(el, { now = () => new Date(), every = (fn, ms) => setInterval(fn, ms) } = {}) {
  if (!el) return false;
  const tick = () => { el.textContent = now().toLocaleTimeString("en-IN"); };
  tick();
  every(tick, 1000);
  return true;
}

if (typeof document !== "undefined") startClock(document.getElementById("liveClock"));
