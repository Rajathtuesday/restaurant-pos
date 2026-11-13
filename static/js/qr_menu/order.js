/*
 * Placing the guest's order from the QR menu.
 *
 * A second round at the same table joins the table's open order: the order's
 * id is kept on this phone (per table token) and sent back. Every request
 * carries its own id (core/request_id.js), so a tap again after "Network
 * error", when the first try may have arrived, cannot make a second order.
 *
 * Returns what happened, for the page to say:
 *   {kind: "placed", data}      the order (and its status token) is kept
 *   {kind: "not-yours"}         the kept order is another table's: forgotten, tap again
 *   {kind: "billed"}            the table's bill is already out: ask a waiter
 *   {kind: "refused", message}  the server's own words (a dish sold out...)
 *   {kind: "network"}           no answer: tap again, the same request is sent
 *   {kind: "busy"}              the first tap is still on its way
 */
import { createRequestIds } from "../core/request_id.js";

export function createOrderSender({ fetchFn, url, csrf, store, requestIds = createRequestIds() }) {
  let busy = false;

  return {
    get busy() {
      return busy;
    },

    async place({ cart, tableId, tableToken, notes }) {
      if (busy) return { kind: "busy" };
      busy = true;
      try {
        const payload = { cart, table_id: tableId || "", table_token: tableToken || "", source: "web", notes: notes || "" };
        const kept = store.getOrderId();
        if (kept) payload.order_id = Number.parseInt(kept, 10);
        const body = requestIds.stamp(payload);
        let response;
        let data;
        try {
          response = await fetchFn(url, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
            body: JSON.stringify(body),
          });
          data = await response.json();
        } catch (noAnswer) {
          return { kind: "network" };       // the same request id goes again on the next tap
        }
        requestIds.settle();
        if (response.ok && data && data.success) {
          store.setOrderId(data.order_id);
          store.setStatusToken(data.status_token);
          return { kind: "placed", data };
        }
        const error = (data && data.error) || "";
        if (error.includes("does not belong")) {
          store.clearOrderId();
          return { kind: "not-yours" };
        }
        if (error.includes("already been billed")) {
          store.clearOrderId();
          return { kind: "billed" };
        }
        return { kind: "refused", message: error || "Could not place the order." };
      } finally {
        busy = false;
      }
    },
  };
}

/* Where this phone keeps a table's order: the order to add to, and the order
   whose status to show (kept longer: until pickup, across reloads). */
export function createOrderStore(storage, tableToken) {
  const orderKey = tableToken ? `rasova_order_${tableToken}` : null;
  const statusKey = tableToken ? `rasova_order_status_${tableToken}` : null;
  const get = (key) => {
    try {
      return key ? storage.getItem(key) : null;
    } catch (blocked) {
      return null;
    }
  };
  const set = (key, value) => {
    try {
      if (key) storage.setItem(key, value);
    } catch (blocked) { /* a private window: this visit only */ }
  };
  const drop = (key) => {
    try {
      if (key) storage.removeItem(key);
    } catch (blocked) { /* as above */ }
  };
  return {
    getOrderId: () => get(orderKey),
    setOrderId: (id) => set(orderKey, String(id)),
    clearOrderId: () => drop(orderKey),
    getStatusToken: () => get(statusKey),
    setStatusToken: (token) => { if (token) set(statusKey, token); },
    clearStatusToken: () => drop(statusKey),
  };
}
