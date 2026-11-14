/*
 * Asks for the open orders every few seconds and after every change.
 * Only the newest answer is drawn (an older one arriving late would put a
 * dish back as it was before the tap). A failure goes to onFailure; the
 * board keeps the last orders it drew, never shows "no open orders" for a
 * failure. It uses fetch itself: a poll must not flash the loading bar or
 * raise a toast every 8 seconds while the connection is down.
 */
export function createOrdersLoader({ fetchFn, url, onOrders, onFailure }) {
  let asked = 0;
  async function refresh() {
    const mine = ++asked;
    let found;
    try {
      const response = await fetchFn(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
      let d;
      try {
        d = await response.json();
      } catch (notJson) {
        throw new Error(response.status === 200 ? "Please sign in again." : `Server error (${response.status}).`);
      }
      if (!response.ok || !d || !Array.isArray(d.orders)) throw new Error((d && d.error) || `Server error (${response.status}).`);
      found = d;
    } catch (err) {
      if (mine !== asked) return false;
      onFailure(err.name === "TypeError" ? "Check the connection." : err.message);
      return false;
    }
    if (mine !== asked) return false;
    onOrders(found);
    return true;
  }
  return { refresh };
}
