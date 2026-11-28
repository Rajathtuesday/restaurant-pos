/*
 * The "today" block of the owner's dashboard, refreshed every minute.
 *
 * The server sends each outlet's block ready to show (accounts.dashboard_tiles
 * .today_blocks: the money already written as rupees, the alerts already in
 * words), so this only puts it on the page. Which answer is drawn, and what
 * a sign-out shows, is core/live_feed.js, the reader every live screen
 * shares; a failed refresh leaves the numbers as they were and says so.
 */
import { createLiveFeed } from "../core/live_feed.js";
import { esc } from "../order_edit.js";

export function alertsHtml(alerts) {
  if (!alerts || !alerts.length) {
    return '<span class="today-ok"><i class="bi bi-check-circle"></i> Nothing needs a look</span>';
  }
  // No href: the report it would lead to does not open here; words only.
  return alerts.map((a) => (a.href
    ? `<a class="today-alert is-${esc(a.level)}" href="${esc(a.href)}">${esc(a.words)}</a>`
    : `<span class="today-alert is-${esc(a.level)}">${esc(a.words)}</span>`)).join("");
}

export function clock(date) {
  return `${date.getHours()}:${String(date.getMinutes()).padStart(2, "0")}`;
}

/* Put one outlet's block into its card (an element with [data-field] children). */
export function drawBlock(card, block) {
  const set = (field, value) => {
    const el = card.querySelector(`[data-field="${field}"]`);
    if (el) el.textContent = String(value);
  };
  set("revenue", block.revenue);
  set("orders", block.orders);
  set("avg", block.avg);
  set("tables", block.tables);
  set("kitchen", block.kitchen);
  const alerts = card.querySelector('[data-field="alerts"]');
  if (alerts) alerts.innerHTML = alertsHtml(block.alerts);
}

/*
 * Asks for today's numbers and hands them to `draw`. `say` is the "updated"
 * line. Returns true when it drew.
 */
export function createTodayRefresh({ fetchFn, url, draw, say, now = () => new Date(), ...feedOptions }) {
  const feed = createLiveFeed({
    ...feedOptions,
    fetchFn,
    address: url,
    read: (d) => {
      if (!d || !Array.isArray(d.today)) throw new Error("not today");
      return d.today;
    },
    onData: (today) => {
      draw(today);
      say(`Updated ${clock(now())}`);
    },
    onFailure: () => say("Could not update: these are the last numbers"),
  });
  return feed.refresh;
}
