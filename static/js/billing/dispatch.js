/*
 * Turning the cart into an order on the server.
 *
 *   dine-in   Dispatch: create the order (or add to the table's open one),
 *             apply a parcel wish, send it to the kitchen, then bill it at
 *             once unless it is a dine-in order on a table
 *   counter   Checkout: create the order (or add to the token's), apply a
 *             parcel wish, then go to the bill; the kitchen ticket fires
 *             at payment (pay-first, cook-after)
 *
 * Offline, or if the connection drops before the server answered, the
 * order is queued (core/offline.js) and replayed later. Once the server
 * has answered the order exists, and nothing after that point is ever
 * retried through the queue, or it would be created twice.
 *
 * "The connection dropped" does not say on which side of the server: the
 * order may have been taken and only the answer lost. So every request
 * carries its own id (core/request_id.js), the queued copy keeps it, and
 * the server answers a second arrival with the first order.
 */
import { createRequestIds } from "../core/request_id.js";
import { withId } from "./urls.js";

export function createDispatch({
  cart, parcel, urls, csrf, ui, screen, offlineQueue, win = globalThis,
  fetchFn = (...a) => fetch(...a), isOnline = () => globalThis.navigator.onLine,
  requestIds = createRequestIds(),
}) {
  let sending = false;

  function jsonPost(url, body) {
    return fetchFn(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body),
    });
  }

  function queueOffline(payload, message) {
    return offlineQueue.add(payload)
      .then(() => {
        requestIds.settle();              // the queued copy carries the id from here on
        ui.toast(message, "success");
        cart.clear();
        return { queued: true };
      })
      .catch(() => {
        ui.toast("Could not save order offline on this device. Please note it down manually.", "error");
        return { queued: false };
      });
  }

  /* POST the order. Returns {created: data}, {queued: true}, or {error}. */
  async function create(cartPayload, { busyLabel, idleHtml, offlineMessage, droppedMessage }) {
    if (sending) return { error: "busy" };
    sending = true;
    screen.sending(true, { busyLabel, idleHtml });
    try {
      const payload = requestIds.stamp(cartPayload);
      if (!isOnline()) return await queueOffline(payload, offlineMessage);
      let d;
      try {
        const r = await jsonPost(urls.create_order, payload);
        d = await r.json();
      } catch (err) {
        // Only the create call itself can land here. Whether the server
        // took the order before the connection failed cannot be known, so
        // the copy that is queued carries the same request id: if it did,
        // the replay is answered with that order. A TypeError is the
        // connection failing, the same test core/http.js uses; anything
        // else is not retried blindly, and a second tap sends the same id.
        if (err instanceof TypeError) return await queueOffline(payload, droppedMessage);
        ui.toast("Could not create order. Please try again.", "error");
        return { error: "network" };
      }
      requestIds.settle();                // the server answered, yes or no
      if (!d.success) {
        ui.toast(d.error || "Could not create order.", "error");
        return { error: d.error };
      }
      return { created: d };
    } finally {
      sending = false;
      screen.sending(false, { busyLabel, idleHtml });
    }
  }

  const dineInIdle = 'Dispatch <i class="bi bi-arrow-right ms-2"></i>';
  const tokenIdle = 'Checkout <i class="bi bi-arrow-right ms-1"></i>';

  return {
    get sending() {
      return sending;
    },

    /* Dine-in: the Dispatch button. */
    async dispatch({ tableId, source, aggregatorId, orderId }) {
      if (sending) return;
      if (cart.isEmpty()) {
        screen.flash("Empty order.");
        return;
      }
      const payload = { cart: cart.toPayload(), table_id: tableId, source, aggregator_id: aggregatorId, customer_name: "", customer_phone: "" };
      if (orderId) payload.order_id = orderId;

      const result = await create(payload, {
        busyLabel: "Dispatching...", idleHtml: dineInIdle,
        offlineMessage: "Saved. It will be sent to the kitchen once back online.",
        droppedMessage: "Connection dropped. The order is saved and will be sent to the kitchen once back online.",
      });
      if (!result.created) return result;
      const d = result.created;

      await parcel.applyTo(d.order_id, { fetchFn, csrf });
      let kitchenNote = "Order Sent";
      try {
        const k = await (await fetchFn(withId(urls.send_to_kitchen, d.order_id), { method: "POST", headers: { "X-CSRFToken": csrf() } })).json();
        if (k.error) kitchenNote = k.error;
      } catch (kitchenLater) {
        // The order exists, so the cart still clears (sending it again
        // would add every dish twice), but the kitchen has not heard.
        kitchenNote = "Order saved, but the kitchen was not reached. Check with the kitchen.";
      }
      screen.flash(kitchenNote);
      cart.clear();
      return { orderId: d.order_id, billNow: source !== "dine_in" || !tableId };
    },

    /* The counter: the Checkout button. */
    async checkout({ orderId, customerPhone }) {
      if (sending) return;
      if (cart.isEmpty()) {
        // Nothing new, but an order with approved items can still be billed
        // from this same button.
        if (orderId) return this.bill(orderId);
        ui.toast("Cart is empty", "warning");
        return;
      }
      const payload = { cart: cart.toPayload(), order_id: orderId, source: "counter", aggregator_id: "", customer_name: "", customer_phone: customerPhone || "" };
      const result = await create(payload, {
        busyLabel: '<span class="spinner-border spinner-border-sm me-2"></span>Processing…', idleHtml: tokenIdle,
        offlineMessage: "Saved. It will sync once back online.",
        droppedMessage: "Connection dropped. The order is saved and will sync once back online.",
      });
      if (!result.created) return result;
      await parcel.applyTo(result.created.order_id, { fetchFn, csrf });
      cart.clear();
      screen.closeMobileCart();
      return this.bill(result.created.order_id);
    },

    /* Issue the bill and go to it. */
    async bill(orderId) {
      if (!orderId) {
        ui.toast("No active order to bill.", "warning");
        return;
      }
      try {
        const r = await fetchFn(withId(urls.generate_bill, orderId), { method: "POST", headers: { "X-CSRFToken": csrf() } });
        const data = await r.json();
        if (data.success) {
          win.location.href = withId(urls.bill_view, data.order_id);
        } else {
          ui.toast(data.error || "Failed to generate bill.", "error");
        }
      } catch (gone) {
        ui.toast("Error generating bill.", "error");
      }
    },
  };
}
