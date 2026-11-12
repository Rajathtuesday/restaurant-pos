/*
 * The parcel charge on a billing screen.
 *
 * Before an order exists the toggle is only a wish the screen remembers,
 * and the cart shows what the server will charge for it, by the server's
 * own rule (orders/views/order_actions.py, toggle_parcel):
 *   a dish with its own parcel charge: that, times its quantity; if no
 *   dish in the cart has one, the outlet's rate times every item when the
 *   outlet charges per item, else the outlet's rate once.
 * Once the order exists the server owns the charge, and what it answers
 * is the amount. A wish is applied to a new order right after it is
 * created, once.
 */
import { withId } from "./urls.js";

export function createParcel({ rate = 0, perItem = false, api, toggleUrl, onChange = () => {} }) {
  const flat = Number(rate) || 0;
  const state = { on: false, appliedTo: null, serverAmount: null };

  function estimate(items) {
    const own = items.reduce((sum, i) => sum + (Number(i.parcel_charge) > 0 ? Number(i.parcel_charge) * i.quantity : 0), 0);
    if (own > 0) return own;
    if (perItem) return flat * items.reduce((sum, i) => sum + i.quantity, 0);
    return flat;
  }

  function fromServer(on, orderId, amount) {
    state.on = !!on;
    state.appliedTo = state.on ? orderId : null;
    state.serverAmount = state.on ? Number(amount) || 0 : null;
  }

  return {
    get on() {
      return state.on;
    },
    /* The outlet's rate, as the button offers it. */
    get rate() {
      return flat;
    },

    /* The charge to show with these cart lines: nothing when off, the
       server's amount once it has spoken, else what it will charge. */
    chargeFor(items = []) {
      if (!state.on) return 0;
      if (state.serverAmount !== null) return state.serverAmount;
      return estimate(items);
    },

    async toggle(orderId) {
      if (!orderId) {
        state.on = !state.on;
        state.appliedTo = null;
        state.serverAmount = null;
        onChange();
        return true;
      }
      try {
        const d = await api.post(withId(toggleUrl, orderId), {});
        fromServer(d.parcel_on, orderId, d.parcel_amount);
        onChange();
        return true;
      } catch (shown) {
        return false; // api showed the error
      }
    },

    /* What the server says about an order the screen just loaded. */
    syncFrom(order) {
      if (order && order.order_id) {
        fromServer(order.parcel_on, order.order_id, order.parcel_amount);
      } else {
        // No open order here: a wish from another table must not carry over.
        fromServer(false, null, 0);
      }
      onChange();
    },

    /* A wish made before the order existed, applied to the new order. */
    async applyTo(orderId, { fetchFn, csrf }) {
      if (!state.on || state.appliedTo === orderId) return;
      try {
        const r = await fetchFn(withId(toggleUrl, orderId), { method: "POST", headers: { "X-CSRFToken": csrf() } });
        if (!r.ok) return;
        const d = await r.json().catch(() => null);
        state.appliedTo = orderId;
        if (d && d.parcel_amount != null) state.serverAmount = Number(d.parcel_amount) || 0;
        onChange();
      } catch (orderExistsAnyway) {
        /* the order is created; the charge can be toggled from the bill */
      }
    },
  };
}
