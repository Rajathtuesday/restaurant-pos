/*
 * The owner's dashboard: today's numbers every minute (and on the refresh
 * button), the New Order tile of a direct-billing counter, and the Zomato
 * and Swiggy switches in the header. Addresses come from page-data
 * (accounts.views.dashboard_views.owner_dashboard).
 */
import { api, ui } from "../core/app.js";
import { delegate, onReady, qsa } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { createTodayRefresh, drawBlock } from "./today.js";

const data = pageData("page-data");
const urls = data.urls || {};
const updated = document.querySelector('#today [data-field="updated"]');

const refresh = createTodayRefresh({
  fetchFn: (...args) => fetch(...args),
  url: urls.metrics,
  draw: (blocks) => {
    // A new outlet or one fewer since the page was drawn: draw the page again.
    const cards = qsa("#today [data-today]");
    if (cards.length !== blocks.length) {
      window.location.reload();
      return;
    }
    blocks.forEach((block, i) => drawBlock(cards[i], block));
  },
  say: (words) => { if (updated) updated.textContent = words; },
});

let starting = false;
const switching = new Set();

delegate(document.body, {
  "dashboard.refresh": () => refresh(),

  "dashboard.new-order": async (button) => {
    if (starting) return;
    starting = true;
    button.disabled = true;
    try {
      const answer = await api.post(urls.new_order, {});
      if (answer.redirect) {
        window.location.href = answer.redirect;
        return;
      }
    } catch (shown) {
      /* api showed the refusal */
    }
    starting = false;
    button.disabled = false;
  },

  "aggregator.toggle": async (button) => {
    const platform = button.dataset.platform;
    if (switching.has(platform)) return;
    switching.add(platform);
    const on = !button.classList.contains("is-on");
    try {
      await api.post(urls.toggle_aggregator, { platform, enabled: on });
      button.classList.toggle("is-on", on);
      button.setAttribute("aria-pressed", String(on));
      ui.toast(`${platform === "zomato" ? "Zomato" : "Swiggy"} orders ${on ? "on" : "off"}`, "success");
    } catch (shown) {
      /* api showed the refusal; the switch stays as it was */
    } finally {
      switching.delete(platform);
    }
  },
});

onReady(() => {
  if (!updated) return;          // a cashier's dashboard has no today block
  setInterval(() => refresh(), data.refresh_ms || 60000);
});
