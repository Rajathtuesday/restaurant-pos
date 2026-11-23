/*
 * Taking a payment on the bill page.
 *
 *   online    the server records it (pay-order); then the bill is printed
 *             (bill_print.js) and the page reloads, with ?print=1 if no
 *             printer took it, which opens the receipt page
 *   offline   only cash: it is queued (core/offline.js) and replayed when
 *             the connection is back. UPI and card need the server to
 *             confirm the money moved.
 *
 * A fine-dining payment needs an open cash register. When the server
 * refuses for that reason, the cashier is offered to open it here, and the
 * same payment is tried again once.
 *
 * Each payment has an id (request_id). The online attempt and, if the
 * connection drops, its queued copy carry the same one, so a payment the
 * server took before the answer was lost is never recorded twice
 * (Payment.request_key).
 */
import { newRequestId } from "../core/request_id.js";

export function createPayment({
  api, urls, ui, paymentQueue, printer, orderId, askOpenRegister,
  isOnline = () => globalThis.navigator.onLine, go, wait = (ms) => new Promise((r) => setTimeout(r, ms)),
  newId = newRequestId,
}) {
  let busy = false;

  async function queueCash(amount, message, paymentId) {
    try {
      await paymentQueue.add(orderId, amount, paymentId);
      ui.toast(message, "success");
      return { queued: true };
    } catch (noStorage) {
      ui.toast("Could not save payment offline on this device. Please note it down manually.", "error");
      return { queued: false };
    }
  }

  async function openRegisterThen(retry) {
    const opening = await askOpenRegister();
    if (opening === null) return { refused: "register" };
    try {
      const r = await api.post(urls.open_register, { opening_balance: opening });
      if (!(r && r.success)) return { refused: "register" };
    } catch (shown) {
      return { refused: "register" };   // api showed why
    }
    ui.toast("Cash register opened", "success");
    return retry();
  }

  async function send(method, amount, { mayOpenRegister, paymentId = newId() }) {
    if (!isOnline()) {
      if (method !== "cash") {
        ui.toast("You are offline. Only cash payments can be taken right now.", "error");
        return { refused: "offline" };
      }
      return queueCash(amount, "Offline: cash payment saved, it will sync when back online. Print the receipt manually for now.", paymentId);
    }

    let data;
    try {
      data = await api.post(urls.pay_order, { method, amount, request_id: paymentId });
    } catch (e) {
      // The connection dropped between the check above and the request
      // landing. A TypeError is the network failing (the test core/http.js
      // uses). Only cash is safe to queue after the fact.
      if (e instanceof TypeError && method === "cash") {
        return queueCash(amount, "Connection dropped: cash payment saved, it will sync once back online.", paymentId);
      }
      // api already showed the server's own message. One of them has a way
      // forward: no cash register is open.
      if (mayOpenRegister && /cash session/i.test((e && e.message) || "")) {
        return openRegisterThen(() => send(method, amount, { mayOpenRegister: false, paymentId }));
      }
      return { refused: "server" };
    }
    if (!data.success) {
      ui.toast(data.error || "Payment failed", "error");
      return { refused: "server" };
    }

    ui.toast(data.message || "Payment recorded", "success");
    // The bill prints once, when it is settled. A part payment (one friend's
    // half of a split) printed it too, saying "Paid via UPI" for the whole
    // total with half still owed (the walkthrough recording, 23 Nov 2026).
    const settled = !(Number(data.remaining) > 0);
    const printed = settled ? await printer.afterPayment(orderId) : false;
    await wait(600);                       // long enough to read the toast
    go(printed || !settled ? "" : "?print=1");
    return { paid: true, printed, settled };
  }

  return {
    /* One payment at a time: a second tap while the first is on its way
       must not record the money twice. */
    async pay(method, amount) {
      if (busy) return { refused: "busy" };
      busy = true;
      try {
        return await send(method, amount, { mayOpenRegister: true });
      } finally {
        busy = false;
      }
    },
  };
}
