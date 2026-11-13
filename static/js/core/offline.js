/*
 * What a cashier can do while the internet is down.
 *
 * Two queues in IndexedDB, replayed the moment the connection is back
 * (on the 'online' event and on every page load, since an order queued in
 * a tab that was closed still has to reach the server):
 *
 *   the order queue     a new order; recreated with /create-order/ and
 *                       sent to the kitchen
 *   the payment queue   a cash payment closing an order; replayed with /pay/
 *
 * They are deliberately two separate things and not one "queue any POST".
 * An order the server refuses (its table or dish is gone by the time the
 * connection returns) can be dropped and re-entered. A payment is money:
 * it is never retried against a different order than the one the cashier
 * closed, and a refusal is shown, not swallowed. UPI and card are not
 * queueable at all: both need the gateway to confirm the money moved, and
 * no queue can confirm that after the fact. Cash is the one method the
 * cashier can confirm by hand, and the one a restaurant falls back to when
 * the internet or the card machine is down anyway.
 *
 * A queued order carries a request id (core/request_id.js). The server
 * answers an id it has already taken with the first order, so an order
 * whose answer was lost, or that two tabs replay at once, is still one
 * order.
 *
 * Storage is behind a tiny interface (add, all, remove) so the queues are
 * tested in node with memoryStore(); the browser uses indexedDbStore().
 */

import { newRequestId } from "./request_id.js";

const DB_NAME = "rasova_offline";
const DB_VERSION = 2; // v2 added the offline_payments store
export const ORDER_STORE = "offline_orders";
export const PAYMENT_STORE = "offline_payments";

export function openOfflineDb(idb = globalThis.indexedDB) {
  return new Promise((resolve, reject) => {
    if (!idb) {
      reject(new Error("IndexedDB unsupported"));
      return;
    }
    const req = idb.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(ORDER_STORE)) {
        db.createObjectStore(ORDER_STORE, { keyPath: "id", autoIncrement: true });
      }
      if (!db.objectStoreNames.contains(PAYMENT_STORE)) {
        db.createObjectStore(PAYMENT_STORE, { keyPath: "id", autoIncrement: true });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = () => reject(req.error);
  });
}

export function indexedDbStore(storeName, idb) {
  const open = () => openOfflineDb(idb);
  return {
    async add(record) {
      const db = await open();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, "readwrite");
        const req = tx.objectStore(storeName).add(record);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    },
    async all() {
      const db = await open();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, "readonly");
        const req = tx.objectStore(storeName).getAll();
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    },
    async remove(id) {
      const db = await open();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, "readwrite");
        tx.objectStore(storeName).delete(id);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    },
  };
}

/* The same interface in memory, for tests and for a browser without IndexedDB. */
export function memoryStore() {
  const rows = new Map();
  let next = 1;
  return {
    async add(record) {
      const id = next++;
      rows.set(id, { id, ...record });
      return id;
    },
    async all() {
      return Array.from(rows.values());
    },
    async remove(id) {
      rows.delete(id);
    },
  };
}

function jsonHeaders(csrf) {
  return { "Content-Type": "application/json", "X-CSRFToken": csrf() };
}

export function createOrderQueue({ store, fetchFn, csrf, ui, isOnline, onChange = () => {}, newId = newRequestId }) {
  let flushing = false;
  return {
    async add(payload) {
      // The id is fixed here, once, and stored with the order: every
      // replay sends the same one. The billing screen has already given
      // the order its id (it may have reached the server); anything else
      // gets one now.
      const stamped = payload && payload.request_id ? payload : { ...payload, request_id: newId() };
      const id = await store.add({ payload: stamped, queued_at: Date.now() });
      await onChange();
      return id;
    },
    all: () => store.all(),
    remove: (id) => store.remove(id),

    async flush() {
      // 'online' can fire while the flush from page load is still running.
      // The guard goes up before the first await: set after it, both
      // flushes passed the check and an order could be created twice (the
      // inline version of this code had that window).
      if (flushing || !isOnline()) return;
      flushing = true;
      try {
        let records;
        try {
          records = await store.all();
        } catch (noStorage) {
          return;
        }
        for (const record of records) {
          let created;
          try {
            created = await fetchFn("/create-order/", {
              method: "POST",
              headers: jsonHeaders(csrf),
              body: JSON.stringify(record.payload),
            });
          } catch (connectionDropped) {
            break; // the rest stays queued for the next 'online' or page load
          }

          let data;
          try {
            data = await created.json();
          } catch (notJson) {
            break; // the answer was lost or was not ours: the next replay sends the same request id
          }

          if (!created.ok || !data.success) {
            // The server refused the order itself (the table or a dish is
            // gone). Retrying forever would never succeed: drop it and say so.
            await store.remove(record.id);
            ui.toast(
              "A saved offline order could not be recreated ("
                + (data && data.error ? data.error : "server rejected it")
                + "). Please re-enter it.",
              "error",
            );
            continue;
          }

          // The order exists now. Sending it to the kitchen is best effort:
          // if this call fails the order is still dropped from the queue, or a
          // retry would create it twice. It can be sent from the running order.
          try {
            await fetchFn("/send-to-kitchen/" + data.order_id + "/", {
              method: "POST",
              headers: { "X-CSRFToken": csrf() },
            });
          } catch (kitchenLater) {
            /* see above */
          }

          await store.remove(record.id);
          ui.toast("Order #" + (data.order_number || data.order_id) + " synced and sent to kitchen", "success");
        }
      } finally {
        flushing = false;
        onChange();
      }
    },
  };
}

export function createPaymentQueue({ store, fetchFn, csrf, ui, isOnline, onChange = () => {} }) {
  let flushing = false;
  return {
    async add(orderId, amount) {
      const id = await store.add({ order_id: orderId, amount, queued_at: Date.now() });
      await onChange();
      return id;
    },
    all: () => store.all(),
    remove: (id) => store.remove(id),

    async flush() {
      if (flushing || !isOnline()) return;
      flushing = true;    // before the first await, see the order queue
      try {
        let records;
        try {
          records = await store.all();
        } catch (noStorage) {
          return;
        }
        for (const record of records) {
          let paid;
          try {
            paid = await fetchFn("/pay/" + record.order_id + "/", {
              method: "POST",
              headers: jsonHeaders(csrf),
              body: JSON.stringify({ method: "cash", amount: record.amount }),
            });
          } catch (connectionDropped) {
            break; // never guess whether a payment we could not confirm went through
          }

          let data;
          try {
            data = await paid.json();
          } catch (notJson) {
            break;
          }

          if (!paid.ok || !data.success) {
            // The order changed while this device was offline (another device
            // closed or cancelled it). This is money, so it is never dropped
            // quietly: it is shown and left to the cashier to settle by hand.
            ui.toast(
              "Offline cash payment for order #" + record.order_id + " could not be synced ("
                + (data && data.error ? data.error : "server rejected it")
                + "). Please check this order manually.",
              "error",
            );
            await store.remove(record.id);
            continue;
          }

          await store.remove(record.id);
          ui.toast("Cash payment for order #" + record.order_id + " synced", "success");
        }
      } finally {
        flushing = false;
        onChange();
      }
    },
  };
}

/* The strip at the top of the page: offline, or syncing N saved items. */
export function createOfflineBanner({ el, queues, isOnline }) {
  return {
    async update() {
      if (!el) return;
      let count = 0;
      for (const queue of queues) {
        try {
          count += (await queue.all()).length;
        } catch (noStorage) {
          /* nothing queued that we can see */
        }
      }
      const items = count + " item" + (count > 1 ? "s" : "");
      if (!isOnline()) {
        el.innerHTML = '<i class="bi bi-wifi-off me-2"></i>You are currently offline.'
          + (count ? " " + items + " saved, will sync when reconnected." : " Actions may not be saved.");
        el.style.display = "block";
      } else if (count) {
        el.innerHTML = '<i class="bi bi-arrow-repeat me-2"></i>Syncing ' + items + "...";
        el.style.display = "block";
      } else {
        el.style.display = "none";
      }
    },
  };
}
