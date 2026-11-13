/*
 * The QSR counter without a kitchen screen or printer: pay and print the
 * slip in one go. The order is created, its total worked out by issuing
 * the bill, the payment taken in a sheet, then the slip printed through
 * the agent (or the receipt page opens if no agent answers). The kitchen
 * ticket fires on the server at payment.
 *
 * Offline, only cash can be taken: the queue replays it later. UPI and
 * card need the gateway to confirm the money moved.
 */
import { send as approvalSend } from "../approval.js";
import { createRequestIds } from "../core/request_id.js";
import { withId } from "./urls.js";

export function createCounter({
  cart, parcel, urls, csrf, ui, screen, printer, offlinePaymentQueue,
  doc = document, win = globalThis, fetchFn = (...a) => fetch(...a), isOnline = () => globalThis.navigator.onLine,
  requestIds = createRequestIds(),
}) {
  let pendingOrderId = null;
  let method = null;
  let total = 0;
  let sending = false;

  const el = (id) => doc.getElementById(id);
  const modal = () => win.bootstrap.Modal.getOrCreateInstance(el("qsrPayModal"));
  const idleHtml = '<i class="bi bi-cash-coin me-2"></i>PAY &amp; PRINT SLIP';
  const payIdle = '<i class="bi bi-printer me-2"></i>CONFIRM &amp; PRINT SLIP';

  function jsonPost(url, body) {
    return fetchFn(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    });
  }

  function reset() {
    cart.clear();
    pendingOrderId = null;
    method = null;
    total = 0;
  }

  function payButton(disabled, html) {
    const btn = el("qsrPayBtn");
    if (!btn) return;
    btn.disabled = disabled;
    if (html) btn.innerHTML = html;
  }

  return {
    /* The big green button: create the order and open the payment sheet. */
    async start({ source }) {
      if (sending) return;
      if (cart.isEmpty()) {
        screen.flash("Add items first.");
        return;
      }
      sending = true;
      screen.sending(true, { busyLabel: "Creating order…", idleHtml });
      try {
        // The same cart sent again after "Network error" carries the same
        // id, so an order the server did take is not made a second time.
        const order = requestIds.stamp({ cart: cart.toPayload(), source, table_id: null });
        const d1 = await (await jsonPost(urls.create_order, order)).json();
        requestIds.settle();              // the server answered, yes or no
        if (!d1.success) {
          screen.flash(d1.error || "Order creation failed");
          return;
        }
        pendingOrderId = d1.order_id;
        await parcel.applyTo(pendingOrderId, { fetchFn, csrf });

        const d2 = await (await fetchFn(withId(urls.generate_bill, pendingOrderId), { method: "POST", headers: { "X-CSRFToken": csrf() } })).json();
        if (!d2.success) {
          screen.flash(d2.error || "Could not calculate total");
          return;
        }
        total = Number(d2.grand_total) || 0;
        el("qsrGrandTotal").textContent = "₹ " + total.toFixed(2);
        el("qsrAmount").value = "";
        el("qsrChangeLine").style.display = "none";
        method = null;
        doc.querySelectorAll(".qsr-method-btn").forEach((b) => b.classList.remove("is-selected"));
        payButton(true, payIdle);
        modal().show();
      } catch (gone) {
        screen.flash("Network error. Try again.");
      } finally {
        sending = false;
        screen.sending(false, { busyLabel: "", idleHtml });
      }
    },

    selectMethod(btn) {
      method = btn.dataset.method;
      doc.querySelectorAll(".qsr-method-btn").forEach((b) => b.classList.toggle("is-selected", b === btn));
      if (method !== "cash") el("qsrAmount").value = total.toFixed(2);
      this.updateChange();
    },

    updateChange() {
      const amount = parseFloat(el("qsrAmount").value) || 0;
      const line = el("qsrChangeLine");
      if (amount >= total && method) {
        const change = amount - total;
        line.style.display = change > 0 ? "block" : "none";
        if (change > 0) line.textContent = "Change due: ₹" + change.toFixed(2);
        payButton(false);
      } else {
        line.style.display = "none";
        payButton(!(method && amount > 0));
      }
    },

    async pay() {
      if (!pendingOrderId || !method) return;
      const amount = parseFloat(el("qsrAmount").value);
      if (!amount || amount <= 0) {
        screen.flash("Enter amount");
        return;
      }
      const orderId = pendingOrderId;

      if (!isOnline()) {
        if (method !== "cash") {
          screen.flash("You are offline. Only cash payments can be taken right now.");
          return;
        }
        try {
          await offlinePaymentQueue.add(orderId, amount.toFixed(2));
          ui.toast("Offline: cash payment saved, it will sync when back online. Print the slip manually for now.", "success");
          modal().hide();
          reset();
        } catch (noStorage) {
          screen.flash("Could not save payment offline on this device. Please note it down manually.");
        }
        return;
      }

      payButton(true, '<i class="bi bi-hourglass-split me-2"></i>Processing…');
      try {
        const d = await (await jsonPost(withId(urls.pay_order, orderId), { method, amount: amount.toFixed(2) })).json();
        if (!d.success) {
          screen.flash(d.error || "Payment failed");
          payButton(false, payIdle);
          return;
        }
        modal().hide();

        try {
          await printer.printBill(orderId);
        } catch (printErr) {
          // No agent, or no printer set up: the receipt page opens instead.
          const { url, blocked } = printer.openReceipt(orderId);
          if (blocked) ui.toast('Popup blocked. <a href="' + url + '" target="_blank" rel="noopener">Open the receipt</a>', "warning");
          if (printErr.message === "agent-offline") ui.toast("Agent not running: start rasova_agent.py on the billing laptop", "warning");
        }

        const confirm = el("qsrConfirm");
        el("qsrConfirmNum").textContent = "Order " + (d.order_number || orderId);
        const change = parseFloat(d.change_due || 0);
        el("qsrChangeDue").textContent = change > 0 ? "Change: ₹" + change.toFixed(2) : "";
        confirm.style.display = "flex";
        setTimeout(() => {
          confirm.style.display = "none";
          reset();
        }, 2500);
      } catch (e) {
        // The connection dropped mid-request. Only cash is safe to queue
        // after the fact.
        if (e instanceof TypeError && method === "cash") {
          try {
            await offlinePaymentQueue.add(orderId, amount.toFixed(2));
            ui.toast("Connection dropped: cash payment saved, it will sync once back online.", "success");
            modal().hide();
            reset();
            return;
          } catch (noStorage) {
            screen.flash("Could not save payment offline on this device. Please note it down manually.");
          }
        } else {
          screen.flash("Network error during payment");
        }
        payButton(false, payIdle);
      }
    },

    /* The total was worked out (a bill number issued), so cancelling needs
       a manager's PIN like any printed bill; refused, the order stays. */
    async cancel() {
      if (!pendingOrderId) {
        modal().hide();
        return;
      }
      try {
        const res = await approvalSend((extra) => fetchFn(withId(urls.cancel_order, pendingOrderId), {
          method: "POST", headers: { "X-CSRFToken": csrf(), ...extra },
        }));
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          screen.flash(d.error || "Could not cancel the order.");
          return;
        }
      } catch (gone) {
        screen.flash("Network error, the order was not cancelled.");
        return;
      }
      modal().hide();
      reset();
    },

    get pendingOrderId() {
      return pendingOrderId;
    },
  };
}
