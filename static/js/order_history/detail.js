/*
 * The order detail panel's markup (orders/order_history.html), from the
 * answer of orders.views.history_views.order_detail_api. Pure: node tests
 * it (static/js/tests/order_history.test.mjs).
 *
 * Everything a person typed (a guest's name, a dish, a refund reason, an
 * audit line) is escaped: it reaches the page as text, never as HTML.
 */
import { esc } from "../order_edit.js";

const STATUSES = new Set(["open", "billing", "closed", "paid", "cancelled"]);

const rs = (n) => "Rs." + Number(n || 0).toFixed(0);
const row = (name, amount, cls = "") =>
  `<div class="detail-row${cls ? " " + cls : ""}"><span class="name">${name}</span><span class="amt">${amount}</span></div>`;

/* What can still be refunded on a payment: its amount less every refund
   not rejected. */
export function remaining(payment) {
  const taken = payment.refunds.filter((r) => r.status !== "rejected").reduce((sum, r) => sum + r.amount, 0);
  return Math.max(0, payment.amount - taken);
}

export const loadingHtml = () => '<div class="loading-detail"><i class="bi bi-hourglass-split"></i> Loading...</div>';
export const messageHtml = (text) => `<p class="detail-error">${esc(text)}</p>`;

function info(o) {
  const status = STATUSES.has(o.status) ? ` st-${o.status}` : "";
  return `<div class="detail-section"><div class="detail-section-title">Order Info</div>` +
    row("Date", esc(o.created_at)) +
    row("Location", esc(o.location)) +
    row("Source", esc(String(o.source || "").replace(/_/g, " "))) +
    row("Waiter / Staff", esc(o.waiter || "-")) +
    (o.customer_name ? row("Customer", esc(o.customer_name)) : "") +
    (o.customer_phone ? row("Phone", esc(o.customer_phone)) : "") +
    (o.duration_mins !== null && o.duration_mins !== undefined ? row("Duration", `${esc(o.duration_mins)} min`) : "") +
    `<div class="detail-row"><span class="name">Status</span><span class="amt detail-status${status}">${esc(o.status)}</span></div>` +
    `</div>`;
}

function items(o) {
  let html = `<div class="detail-section"><div class="detail-section-title">Items (${o.items.length})</div>`;
  if (!o.items.length) return html + `<p class="detail-none">No items recorded.</p></div>`;
  for (const item of o.items) {
    const voided = item.status === "voided";
    const cls = voided ? "voided" : item.is_complimentary ? "comp" : "";
    const tag = voided ? '<span class="tag-void">VOID</span>' : item.is_complimentary ? '<span class="tag-comp">COMP</span>' : "";
    const note = item.void_reason ? `<span class="void-reason">Reason: ${esc(item.void_reason)}</span>` : "";
    html += row(`${esc(item.quantity)}× ${esc(item.name)}${tag}${note}`, item.is_complimentary ? "Rs.0" : rs(item.total), cls);
  }
  return html + `</div>`;
}

function totals(o) {
  return `<div class="detail-section"><div class="detail-section-title">Totals</div>` +
    row("Subtotal", rs(o.subtotal)) +
    row("GST", rs(o.gst_total)) +
    (o.discount_total > 0 ? row("Discount", `<span class="detail-discount">-${rs(o.discount_total)}</span>`) : "") +
    row("Grand Total", rs(o.grand_total), "total-row") +
    `</div>`;
}

function payments(o) {
  if (!o.payments.length) return "";
  let html = `<div class="detail-section"><div class="detail-section-title">Payments</div>`;
  for (const p of o.payments) {
    const label = esc(String(p.method).toUpperCase()) + (p.reference ? ` · ${esc(p.reference)}` : "");
    const at = `<span class="detail-at">${esc(p.paid_at)}</span>`;
    if (p.is_refund) {
      html += row(label, `-${rs(Math.abs(p.amount))} ${at}`, "refund-row");
      continue;
    }
    const left = remaining(p);
    const button = o.can_refund && left > 0
      ? ` <button type="button" class="btn-refund" data-action="oh.refund" data-payment="${esc(p.id)}" data-max="${left.toFixed(2)}">Refund</button>`
      : "";
    html += row(label + button, `${rs(p.amount)} ${at}`);
    for (const r of p.refunds) {
      const decide = o.can_approve_refund && r.status === "pending"
        ? `<button type="button" class="btn-approve" data-action="oh.approve" data-refund="${esc(r.id)}">Approve</button>` +
          `<button type="button" class="btn-reject" data-action="oh.reject" data-refund="${esc(r.id)}">Reject</button>`
        : "";
      html += `<div class="refund-entry">` +
        `<span>₹${Number(r.amount).toFixed(0)} · ${esc(r.reason || "No reason")} <span class="detail-at">by ${esc(r.by)}</span></span>` +
        `<span class="refund-side"><span class="refund-status status-${esc(r.status)}">${esc(r.status)}</span>${decide}</span>` +
        `</div>`;
    }
  }
  return html + `</div>`;
}

function audit(o) {
  if (!o.events || !o.events.length) return "";
  let html = `<div class="detail-section"><div class="detail-section-title">Audit Trail</div>`;
  for (const ev of o.events) {
    html += `<div class="audit-event"><span class="audit-time">${esc(ev.at)}</span>` +
      `<span class="audit-label">${esc(ev.label)}</span><span class="audit-by">${esc(ev.by || "System")}</span></div>`;
  }
  return html + `</div>`;
}

export function detailHtml(o, { receiptUrl }) {
  return info(o) + items(o) + totals(o) + payments(o) + audit(o) +
    `<div class="detail-reprint"><a href="${esc(receiptUrl)}" target="_blank" rel="noopener" class="btn-luxury w-100">` +
    `<i class="bi bi-printer"></i> Reprint Receipt</a></div>`;
}
