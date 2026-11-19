/*
 * The bill page: discounts, taking payment, printing, loyalty points.
 *
 * The page gives this module what it needs in one json_script block,
 * page-data (orders.views.billing_core.bill_page_data): the order, what is
 * left to pay, the printing settings and every address it calls. Buttons
 * carry data-action; this is the one place that handles them.
 */
import { esc } from "../order_edit.js";
import { api, ui, theme, platform, paymentQueue } from "../core/app.js";
import { csrfToken } from "../core/csrf.js";
import { delegate, onReady } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { changeDue, payable, upiString } from "./amount.js";
import { rupees } from "../core/money.js";
import { createBillPrinter } from "./bill_print.js";
import { createDialogs } from "./dialogs.js";
import { billDiscountProblem, promoLabel, promoProblem } from "./discounts.js";
import { createPayment } from "./payment.js";
import { createRazorpayQr } from "./razorpay_qr.js";

const data = pageData("page-data");
const urls = data.urls;
const el = (id) => document.getElementById(id);
const reload = () => window.location.reload();

const dialogs = createDialogs({ isDark: theme.isDark });
const printer = createBillPrinter({
  platform, urls, ui, csrf: () => csrfToken(),
  settings: {
    splitMode: data.split_mode, useAgent: data.use_agent, printerName: data.printer_name,
    printerMac: data.printer_mac, outletId: data.outlet_id,
  },
});
const payment = createPayment({
  api, urls, ui, paymentQueue, printer, orderId: data.order_id,
  askOpenRegister: () => dialogs.openRegister(),
  go: (query) => { window.location.href = window.location.pathname + query; },
});
const razorpay = createRazorpayQr({
  api, urls, ui, onPaid: reload,
  getAmount: () => (el("payAmount") && el("payAmount").value) || data.remaining,
});

/* ---- the amount being collected ---------------------------------------- */

/* The UPI QR carries an amount, so it is redrawn whenever the amount to
   collect changes: typing, Pay All, Split. Without that a split share was
   typed in the field while the QR still asked for the whole bill. */
function drawUpiQr(amount) {
  const box = el("upi-qr-canvas");
  const fallback = el("upi-qr-fallback");
  if (!box || !fallback) return;                 // no UPI id, or nothing left to pay
  box.innerHTML = "";                            // or the new one stacks on the old
  fallback.classList.remove("is-shown");
  const text = upiString({ upiId: data.upi_id, payee: data.payee, amount, billNumber: data.bill_number });
  try {
    window.QRCode.toCanvas(text, { width: 180, margin: 1, color: { dark: "#000000", light: "#ffffff" } }, (err, canvas) => {
      if (err || !canvas) fallback.classList.add("is-shown");
      else box.appendChild(canvas);
    });
  } catch (noLibrary) {
    fallback.classList.add("is-shown");          // never a silent empty box
  }
}

function amountChanged(text) {
  drawUpiQr(Number(text) || 0);
  const display = el("change-display");
  if (!display) return;
  const change = changeDue(text, data.remaining);
  if (change > 0) el("change-amount").textContent = rupees(change);
  display.classList.toggle("is-shown", change > 0);
}

function setAmount(text) {
  el("payAmount").value = text;
  amountChanged(text);
}

/* ---- discounts ---------------------------------------------------------- */

async function applyDiscount(promoId = null) {
  const type = el("discount-type").value;
  const value = parseFloat(el("discount-value").value);
  const reason = promoId ? "" : el("discount-reason").value.trim();
  const problem = billDiscountProblem({ value, reason, promoId });
  if (problem) {
    ui.toast(problem.message, "warning");
    if (problem.field === "reason") el("discount-reason").focus();
    return;
  }
  try {
    const d = await api.post(urls.apply_discount, { type, value, promo_id: promoId, reason });
    if (d.success) reload();
  } catch (shown) { /* api has shown the error */ }
}

function applyPromo(select) {
  if (!select.value) return;
  let promo;
  try {
    promo = JSON.parse(select.value);
  } catch (notAPromo) {
    return;
  }
  select.selectedIndex = 0;
  const problem = promoProblem(promo, data.subtotal);
  if (problem) {
    ui.toast(problem, "warning");
    return;
  }
  el("discount-type").value = promo.discount_type;
  el("discount-value").value = promo.discount_value;
  applyDiscount(promo.id);
}

async function loadPromos() {
  const select = el("promo-select");
  if (!select) return;
  try {
    const d = await (await fetch(urls.list_promos)).json();
    (d.promos || []).forEach((p) => select.appendChild(new Option(promoLabel(p), JSON.stringify(p))));
  } catch (quiet) { /* the picker stays empty; a discount can still be typed */ }
}

/* ---- loyalty ------------------------------------------------------------ */

async function lookupGuest() {
  const phone = (el("guestPhone")?.value || "").trim();
  const info = el("guestInfo");
  if (!phone || !info) return;
  info.classList.add("is-shown");
  info.textContent = "Looking up…";
  try {
    const d = await (await fetch(urls.guest_lookup + "?phone=" + encodeURIComponent(phone))).json();
    if (d.found) {
      info.innerHTML = `<strong>${esc(d.name || "Guest")}</strong> &nbsp;·&nbsp; ${esc(d.visits)} visits &nbsp;·&nbsp; <span class="loyalty-points">${esc(d.points)} pts</span>`;
      if (d.name) el("guestName").value = d.name;
    } else {
      info.textContent = "New guest: registered when points are awarded.";
    }
  } catch (gone) {
    info.textContent = "Lookup failed";
  }
}

async function awardPoints() {
  const phone = (el("guestPhone")?.value || "").trim();
  const msg = el("loyaltyMsg");
  if (!phone) {
    ui.toast("Phone required", "warning");
    return;
  }
  const say = (text, ok) => {
    msg.textContent = text;
    msg.classList.add("is-shown");
    msg.classList.toggle("is-error", !ok);
  };
  try {
    const d = await api.post(urls.link_guest, {
      phone, name: (el("guestName")?.value || "").trim(), redeem_points: parseInt(el("redeemPts")?.value, 10) || 0,
    });
    if (d.success) say(`+${d.points_earned} pts earned! Balance: ${d.total_points} pts`, true);
    else say(d.error || "Failed", false);
  } catch (shown) {
    say("Failed", false);
  }
}

/* ---- the actions --------------------------------------------------------- */

delegate(document.body, {
  // "page.print" is core/page_actions.js, handled by core/app.js.
  "pay.all": () => setAmount(data.remaining),
  "pay.split": async () => {
    const share = await dialogs.split(data.remaining);
    if (share !== null) setAmount(share);
  },
  "pay.method": (button) => {
    const amount = button.dataset.amount !== undefined ? button.dataset.amount : (el("payAmount") ? el("payAmount").value : 0);
    if (payable(amount) === null) {
      ui.toast("Enter the amount to collect.", "warning");
      return;
    }
    payment.pay(button.dataset.method, amount);
  },
  "pay.cover": async () => {
    const answer = await dialogs.coverCode();
    if (!answer) return;
    try {
      const d = await api.post(urls.cover_redeem, answer);
      if (d.success) reload();
    } catch (shown) { /* api has shown the error */ }
  },
  "pay.razorpay": () => razorpay.open(),
  "razorpay.close": () => razorpay.close(),
  "bill.print": () => printer.manual(data.order_id),
  "bill.close_unpaid": async () => {
    const reason = await dialogs.closeUnpaid(data.remaining);
    if (reason === null) return;
    try {
      const d = await api.post(urls.log_bypass, { reason });
      if (d.success) window.location.href = data.after_close;
    } catch (shown) { /* api has shown the error; the bill stays open */ }
  },
  "bill.stay": (link, event) => {
    event.preventDefault();
    ui.toast("Complete payment before leaving", "warning");
  },
  "discount.apply": () => applyDiscount(),
  "discount.remove": async () => {
    try {
      const d = await api.post(urls.apply_discount, { type: "percentage", value: 0 });
      if (d.success) reload();
    } catch (shown) { /* api has shown the error */ }
  },
  "discount.item": async (button) => {
    const answer = await dialogs.itemDiscount(button.dataset.name, button.dataset.pct);
    if (!answer) return;
    try {
      const d = await api.post(button.dataset.url, answer);
      if (d.success) reload();
    } catch (shown) { /* api has shown the error */ }
  },
  "guest.lookup": () => lookupGuest(),
  "guest.award": () => awardPoints(),
});

delegate(document.body, { "discount.promo": (select) => applyPromo(select) }, "change");
delegate(document.body, { "pay.amount": (input) => amountChanged(input.value) }, "input");

/* ---- go ----------------------------------------------------------------- */

onReady(() => {
  drawUpiQr(data.remaining);
  loadPromos();
  if ((el("guestPhone")?.value || "").trim()) lookupGuest();

  // A cashier may not walk away from an unpaid bill with the Back button.
  if (data.guard_back) {
    window.history.pushState(null, "", window.location.href);
    window.addEventListener("popstate", () => {
      window.history.go(1);
      ui.toast("Complete payment before leaving", "warning");
    });
  }

  // Right after a payment no printer took: the receipt page, for the browser to print.
  if (data.auto_print) setTimeout(() => printer.openReceipt(data.order_id), 600);
});
