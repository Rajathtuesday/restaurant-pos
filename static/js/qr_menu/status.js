/*
 * The guest's order status on the QR menu: the pill, and the sheet with the
 * four stages, asked from /menu/order-status/<signed token>/ every few
 * seconds while the table has an order on its way.
 */

/* Every stage the server can send (menu.views.customer_views.order_status)
   has a place on the four-step timeline. */
export const STAGE_INDEX = {
  placed: 0, confirmed: 0, waiting_confirmation: 0,
  preparing: 1, ready: 2, served: 3,
};
const PILL = {
  placed: "Received", confirmed: "Received", waiting_confirmation: "Received",
  preparing: "Preparing", ready: "Ready!", served: "Served",
};

/* What the pill and the sheet show for one answer. */
export function describeStatus(data) {
  const ready = Boolean(data.token_ready);
  return {
    pill: ready ? "Ready for pickup!" : (PILL[data.stage] || "Order status"),
    sheet: ready ? "Ready for pickup!" : (data.label || "Order received"),
    stage: ready ? 2 : (STAGE_INDEX[data.stage] ?? 0),
    done: ready || data.stage === "ready" || data.stage === "served",
    token: data.token_display || null,
    table: data.table || "",
    items: Array.isArray(data.items) ? data.items : [],
  };
}

/* Nothing more to show: cancelled; a table order that can take no more;
   a counter token that was collected. */
export function finished(data) {
  return data.order_status === "cancelled"
    || (!data.token_display && !data.can_add_more)
    || (Boolean(data.token_display) && Boolean(data.token_collected));
}

/*
 * Asks for the status until it is finished. onStatus(described) draws it,
 * onGone() hides it. Only the newest answer is drawn.
 */
export function createStatusPoller({ fetchFn, url, store, onStatus, onGone, every = 6000, timers = globalThis }) {
  let token = null;
  let timer = null;
  let asked = 0;

  function stop() {
    token = null;
    if (timer) timers.clearInterval(timer);
    timer = null;
  }

  async function poll() {
    if (!token) return;
    const mine = ++asked;
    let response;
    try {
      response = await fetchFn(url.replace("TOKEN", encodeURIComponent(token)), { headers: { Accept: "application/json" } });
    } catch (blip) {
      return;                         // the next tick asks again
    }
    if (mine !== asked) return;
    if (!response.ok) {               // the order is gone, or the link expired
      store.clearOrderId();
      store.clearStatusToken();
      stop();
      onGone();
      return;
    }
    let data;
    try {
      data = await response.json();
    } catch (notJson) {
      return;
    }
    if (mine !== asked) return;
    onStatus(describeStatus(data));
    if (!data.can_add_more) store.clearOrderId();     // the next round starts a new order
    if (finished(data)) {
      store.clearStatusToken();
      stop();
      onGone();
    }
  }

  return {
    poll,
    stop,
    /* Start (or restart, after a new order) from the kept status token. */
    start() {
      token = store.getStatusToken();
      if (!token) return false;
      if (timer) timers.clearInterval(timer);
      poll();
      timer = timers.setInterval(poll, every);
      return true;
    },
  };
}
