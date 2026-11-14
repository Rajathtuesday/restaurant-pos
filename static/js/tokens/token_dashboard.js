/*
 * The token dashboard (tokens/token_dashboard.html): New Order (opens its
 * bill), cancelling a token (order_edit.js's sheet), and the 15-second
 * refresh (refresher.js). Mark Ready, Picked Up and the Zomato and Swiggy
 * switches are post buttons (core/post_actions.js).
 */
import { api } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { withId } from "../billing/urls.js";
import OrderEdit from "../order_edit.js";
import { createRefresher } from "./refresher.js";

const NEW_ORDER = '<i class="bi bi-plus-lg" aria-hidden="true"></i> New Order';

function updateActiveCount() {
  const remaining = document.querySelectorAll("#tokenGrid .token-wrap").length;
  const count = document.getElementById("activeCount");
  if (count) count.textContent = remaining;
  const empty = document.getElementById("emptyState");
  if (remaining === 0 && empty) empty.hidden = false;
}

delegate(document.body, {
  "token.new": async (button) => {
    if (button.disabled) return;
    button.disabled = true;
    button.innerHTML = '<i class="bi bi-hourglass-split" aria-hidden="true"></i> Creating…';
    try {
      const data = await api.post(button.dataset.url, {});
      window.location.href = withId(button.dataset.billUrl, data.order_id);
    } catch (shown) {
      button.disabled = false;
      button.innerHTML = NEW_ORDER;
    }
  },
  "token.cancel": async (button, event) => {
    event.preventDefault();
    OrderEdit.configure({ cancelOrder: button.dataset.url });
    const data = await OrderEdit.cancelOrder(button.dataset.order, { billUrl: button.dataset.billUrl });
    if (!data) return;
    const wrap = document.getElementById(`wrap-${button.dataset.order}`);
    if (!wrap) return;
    wrap.classList.add("is-leaving");
    setTimeout(() => { wrap.remove(); updateActiveCount(); }, 300);
  },
});

const bar = document.getElementById("refreshBar");
const refresher = createRefresher({
  reload: () => window.location.reload(),
  busy: () => document.hidden || OrderEdit.sheetOpen(),
  onReset: () => {
    if (!bar) return;
    bar.classList.remove("is-running");
    void bar.offsetHeight;              // restart the countdown animation
    bar.classList.add("is-running");
  },
});
refresher.reset();
for (const name of ["click", "keydown", "touchstart"]) {
  document.addEventListener(name, () => refresher.reset(), { passive: true });
}
