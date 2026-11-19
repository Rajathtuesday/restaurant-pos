/*
 * Order History (orders/order_history.html): a row opens the order's detail
 * panel (detail.js draws it). From there a manager asks for a refund (the
 * refund form) and the owner approves or rejects one, in Rasova's own
 * sheets: the browser's confirm() and prompt() never show in the Android
 * app. Addresses come from page-data
 * (orders.views.history_views.order_history_page_data).
 *
 * The panel keeps the order's id. Its heading shows the bill number, which
 * is not an id, so the old page could not draw the panel again after a
 * refund changed it.
 */
import { api, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { rupees } from "../core/money.js";
import { withId } from "../billing/urls.js";
import { detailHtml, loadingHtml, messageHtml } from "./detail.js";

const data = pageData("page-data");
const overlay = document.getElementById("detailOverlay");
const panel = document.getElementById("detailPanel");
const body = document.getElementById("detailBody");
const heading = document.getElementById("detailOrderNum");
const refundBox = document.getElementById("refundModalOverlay");
const refundForm = document.getElementById("refundForm");
const refundMax = document.getElementById("refundMaxAmt");

let openId = null;
let asked = 0;
let refundPayment = null;
let busy = false;

/* Opens (or redraws) the panel for an order. Only the newest question's
   answer is drawn, so a quick second tap never shows the first order. */
async function show(orderId) {
  openId = orderId;
  const mine = ++asked;
  overlay.classList.add("open");
  panel.classList.add("open");
  body.innerHTML = loadingHtml();
  try {
    const o = await api.get(withId(data.urls.detail, orderId));
    if (mine !== asked) return;
    heading.textContent = o.order_number;
    body.innerHTML = detailHtml(o, { receiptUrl: withId(data.urls.receipt, o.id) });
  } catch (err) {
    if (mine === asked) body.innerHTML = messageHtml(err.message || "Could not load this order.");
  }
}

function closeDetail() {
  asked += 1;
  overlay.classList.remove("open");
  panel.classList.remove("open");
}

function openRefund(button) {
  refundPayment = button.dataset.payment;
  refundForm.reset();
  refundForm.elements.amount.max = button.dataset.max;
  refundMax.textContent = rupees(button.dataset.max);
  refundBox.classList.add("open");
  refundForm.elements.amount.focus();
}

function closeRefund() {
  refundBox.classList.remove("open");
  refundPayment = null;
}

/* Sends one change; on success says so and draws the panel again. A second
   tap while the first is out does nothing. */
async function change(send, said) {
  if (busy) return false;
  busy = true;
  try {
    await send();
  } catch (shown) {
    return false; /* the client showed why */
  } finally {
    busy = false;
  }
  ui.toast(said, "success");
  if (openId) show(openId);
  return true;
}

delegate(document.body, {
  "oh.open": (el) => show(el.dataset.id),
  "oh.close": closeDetail,
  "oh.refund": openRefund,
  "oh.refund-cancel": closeRefund,
  "oh.approve": async (b) => {
    const answer = await ui.confirm("Approve this refund?", "", "Approve");
    if (answer.isConfirmed) change(() => api.post(withId(data.urls.approve, b.dataset.refund), {}), "Refund approved.");
  },
  "oh.reject": async (b) => {
    const reason = await ui.ask("Reject this refund?", { label: "Reason (optional)", confirmText: "Reject", required: false, maxLength: 200 });
    if (reason !== null) change(() => api.post(withId(data.urls.reject, b.dataset.refund), { reason }), "Refund rejected.");
  },
});

delegate(document.body, {
  "oh.open": (el, event) => {
    if (event.key === "Enter") show(el.dataset.id);
  },
}, "keydown");

delegate(document.body, {
  "oh.refund-submit": async (form, event) => {
    event.preventDefault();
    if (!refundPayment) return;
    const f = form.elements;
    const reason = f.reason.value.trim();
    if (!reason) {
      ui.toast("Manager Note is required.", "error");
      f.reason.focus();
      return;
    }
    const body = { amount: f.amount.value, customer_complaint: f.customer_complaint.value.trim(), reason };
    const payment = refundPayment;
    if (await change(() => api.post(withId(data.urls.refund, payment), body), "Refund requested.")) closeRefund();
  },
}, "submit");

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (refundBox.classList.contains("open")) closeRefund();
  else closeDetail();
});
