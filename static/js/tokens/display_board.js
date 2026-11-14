/*
 * The pickup screen (tokens/display_board.html): the tokens ready for
 * pickup, each as a big tile. One long-lived EventSource connection
 * (the server sends only when the list changes and closes now and then;
 * EventSource reconnects by itself); a 4-second poll only where
 * EventSource does not exist, and one poll after a connection error so a
 * stuck connection never freezes the board. The grid names its two
 * addresses (data-stream, data-data).
 */
import { esc } from "../order_edit.js";

export function tilesHtml(tokens) {
  return tokens.map((t) => `
    <div class="token-tile">
      <div class="num">${esc(t.display)}</div>
      <div class="label">Ready for pickup</div>
    </div>`).join("");
}

export function render(grid, empty, data) {
  const tokens = (data && data.tokens) || [];
  grid.innerHTML = tilesHtml(tokens);
  empty.hidden = tokens.length > 0;
}

export function connect({ grid, empty, EventSourceCls, fetchFn, every = (fn, ms) => setInterval(fn, ms) }) {
  const pollOnce = async () => {
    try {
      const response = await fetchFn(grid.dataset.data, { headers: { Accept: "application/json" } });
      render(grid, empty, await response.json());
    } catch (down) {
      /* the next tick tries again */
    }
  };
  if (!EventSourceCls) {
    pollOnce();
    every(pollOnce, 4000);
    return "poll";
  }
  const source = new EventSourceCls(grid.dataset.stream);
  source.onmessage = (e) => {
    try {
      render(grid, empty, JSON.parse(e.data));
    } catch (bad) {
      /* a malformed message: keep what is shown */
    }
  };
  source.onerror = () => pollOnce();
  return "stream";
}

if (typeof document !== "undefined") {
  connect({
    grid: document.getElementById("tokenGrid"),
    empty: document.getElementById("emptyState"),
    EventSourceCls: typeof EventSource === "undefined" ? null : EventSource,
    fetchFn: (...args) => fetch(...args),
  });
}
