/*
 * The token dashboard reloads itself 15 seconds after the last touch, so
 * the counter sees new tokens without tapping. Any tap or key starts the
 * 15 seconds again. It waits, rather than reloading, while a sheet is
 * open (cancelling a token) or the tab is hidden, so a reload never
 * throws away something being done.
 */
export function createRefresher({ reload, busy, schedule = (fn, ms) => setTimeout(fn, ms), cancel = (t) => clearTimeout(t), onReset = () => {}, ms = 15000 }) {
  let timer = null;
  function fire() {
    if (busy()) {
      timer = schedule(fire, ms);
      return false;
    }
    reload();
    return true;
  }
  return {
    fire,
    reset() {
      if (timer !== null) cancel(timer);
      timer = schedule(fire, ms);
      onReset();
    },
  };
}
