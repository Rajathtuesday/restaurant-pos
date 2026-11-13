/*
 * Asks the server for the floor and hands it to the page.
 *
 * The floor is asked for every few seconds and again after every action,
 * so two answers can be on their way at once. Only the answer to the
 * newest question is drawn: an older one arriving late would put the
 * floor back to how it was before the action.
 *
 * It uses fetch itself, not the shared client: a poll must not flash the
 * loading bar or raise a toast every 5 seconds while the connection is
 * down. A failure is shown in the grid instead.
 */
export function createFloorLoader({ fetchFn, url, onTables, onFailure }) {
  let asked = 0;
  let tables = [];

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
      if (!d || !Array.isArray(d.tables)) throw new Error((d && d.message) || "Invalid data from server");
      found = d.tables;
    } catch (err) {
      if (mine !== asked) return false;
      onFailure(err.name === "TypeError" ? "Check the connection." : err.message);
      return false;
    }
    if (mine !== asked) return false;
    tables = found;
    onTables(tables);
    return true;
  }

  return {
    refresh,
    tables: () => tables,
    find: (id) => tables.find((t) => t.id === Number(id)),
  };
}
