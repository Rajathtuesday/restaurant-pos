/*
 * Asks the server for the kitchen's tickets and hands them to the page.
 *
 * The tickets are asked for every few seconds, after every action and
 * when the cook picks a station, so two answers can be on their way at
 * once. Only the answer to the newest question is drawn: an older one
 * arriving late would show the station the cook just left, or put a dish
 * back to how it was before the tap.
 *
 * It uses fetch itself, not the shared client: a poll must not flash the
 * loading bar or raise a toast every 5 seconds while the connection is
 * down. A failure is shown in the grid instead, never as "All Caught Up".
 */
export function createKitchenLoader({ fetchFn, url, onKots, onFailure }) {
  let asked = 0;
  let station = "";

  async function refresh() {
    const mine = ++asked;
    const address = url + (station ? "?station=" + encodeURIComponent(station) : "");
    let found;
    try {
      const response = await fetchFn(address, { headers: { Accept: "application/json" } });
      let d;
      try {
        d = await response.json();
      } catch (notJson) {
        // A login page or an error page came back.
        throw new Error(response.status === 200 ? "Please sign in again." : `Server error (${response.status}).`);
      }
      if (!d || !Array.isArray(d.kots)) throw new Error((d && d.error) || "Invalid data from server.");
      found = d.kots;
    } catch (err) {
      if (mine !== asked) return false;
      onFailure(err.name === "TypeError" ? "Check the connection." : err.message);
      return false;
    }
    if (mine !== asked) return false;
    onKots(found);
    return true;
  }

  return {
    refresh,
    station: () => station,
    /* Show one station's tickets ("" for all of them). */
    setStation(name) {
      station = name || "";
      return refresh();
    },
  };
}
