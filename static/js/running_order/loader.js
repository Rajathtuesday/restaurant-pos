/*
 * Asks the server for the order every few seconds and after every action.
 *
 * Two answers can be on their way at once (a poll and the refresh after a
 * tap), so only the answer to the newest question is drawn: an older one
 * arriving late would put a dish back the way it was before the tap.
 *
 * It uses fetch itself, not the shared client: a poll must not flash the
 * loading bar or raise a toast every 3 seconds while the connection is
 * down. A failure goes to onFailure with words for the person; the page
 * keeps the last list it drew, never shows "no dishes" for a failure.
 */
export function createOrderLoader({ fetchFn, url, onOrder, onFailure }) {
  let asked = 0;

  async function refresh() {
    const mine = ++asked;
    let found;
    try {
      const response = await fetchFn(url, { headers: { Accept: "application/json" } });
      let d;
      try {
        d = await response.json();
      } catch (notJson) {
        // A login page or an error page came back.
        throw new Error(response.status === 200 ? "Please sign in again." : `Server error (${response.status}).`);
      }
      if (!response.ok || !d || !Array.isArray(d.items)) {
        throw new Error((d && d.error) || `Server error (${response.status}).`);
      }
      found = d;
    } catch (err) {
      if (mine !== asked) return false;
      onFailure(err.name === "TypeError" ? "Check the connection." : err.message);
      return false;
    }
    if (mine !== asked) return false;
    onOrder(found);
    return true;
  }

  return { refresh };
}
