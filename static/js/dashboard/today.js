/*
 * The "today" block of the owner's dashboard, refreshed every minute.
 *
 * The server sends each outlet's block ready to show (accounts.dashboard_tiles
 * .today_blocks: the money already written as rupees, the alerts already in
 * words), so this only puts it on the page. Only the newest answer is drawn,
 * and a failed refresh leaves the numbers as they were and says so.
 */
import { esc } from "../order_edit.js";

export function alertsHtml(alerts) {
  if (!alerts || !alerts.length) {
    return '<span class="today-ok"><i class="bi bi-check-circle"></i> Nothing needs a look</span>';
  }
  return alerts.map((a) => `<a class="today-alert is-${esc(a.level)}" href="${esc(a.href)}">${esc(a.words)}</a>`).join("");
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
export function createTodayRefresh({ fetchFn, url, draw, say, now = () => new Date() }) {
  let asked = 0;
  return async function refresh() {
    const mine = ++asked;
    try {
      const response = await fetchFn(url, { headers: { Accept: "application/json" } });
      const data = await response.json();
      if (!response.ok || !Array.isArray(data.today)) throw new Error("not today");
      if (mine !== asked) return false;
      draw(data.today);
      say(`Updated ${clock(now())}`);
      return true;
    } catch (failed) {
      if (mine !== asked) return false;
      say("Could not update: these are the last numbers");
      return false;
    }
  };
}
