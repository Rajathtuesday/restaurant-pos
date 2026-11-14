/*
 * What the live orders board draws (orders/live_orders.html): one ticket
 * per open order with its dishes, the filter counts, and which orders a
 * filter and the search leave. Every value from the server is escaped.
 * Pure functions, tested in node.
 */
import { rupees } from "../core/money.js";
import { esc } from "../order_edit.js";

const STATUS_LABEL = { pending: "Not sent", review: "Approve", sent: "Sent", preparing: "Cooking", ready: "Ready", served: "Served" };
const KIND_LABEL = { table: "Dine-in", token: "Counter", takeaway: "Takeaway", online: "Online" };

export function needsAttention(o) {
  return o.counts.review > 0 || o.counts.ready > 0 || o.age_minutes >= 30;
}

export function group(o) {
  if (o.kind === "table") return "table";
  if (o.kind === "online") return "online";
  return "counter";
}

export function matches(o, filter, query) {
  if (filter === "attention" && !needsAttention(o)) return false;
  if (filter !== "all" && filter !== "attention" && group(o) !== filter) return false;
  if (!query) return true;
  const hay = [o.label, o.staff, String(o.id), o.source_display].concat(o.items.map((i) => i.name)).join(" ").toLowerCase();
  return hay.includes(query);
}

export function counts(orders) {
  const c = { all: orders.length, table: 0, counter: 0, online: 0, attention: 0 };
  for (const o of orders) {
    c[group(o)] += 1;
    if (needsAttention(o)) c.attention += 1;
  }
  return c;
}

export function ageClass(m) {
  return m >= 40 ? "is-late" : m >= 20 ? "is-warn" : "";
}

export function ageText(m) {
  if (m < 60) return m + " min";
  if (m < 1440) return Math.floor(m / 60) + " h " + (m % 60) + " m";
  return Math.floor(m / 1440) + " d " + Math.floor((m % 1440) / 60) + " h";
}

function itemRow(o, i) {
  const subs = i.modifiers.length ? `<div class="lo-name-sub">+ ${esc(i.modifiers.join(", "))}</div>` : "";
  const note = i.notes ? `<div class="lo-name-note">${esc(i.notes)}</div>` : "";
  const comp = i.is_complimentary ? ' <span class="lo-st st-served">Comp</span>' : "";
  let acts = "";
  if (i.can_reduce) {
    acts =
      `<button type="button" class="lo-btn" data-action="order.reduce" data-o="${esc(o.id)}" data-i="${esc(i.id)}" ` +
      `aria-label="Remove one ${esc(i.name)}" title="Remove one"><i class="bi bi-dash-lg" aria-hidden="true"></i></button>` +
      `<button type="button" class="lo-btn is-danger" data-action="order.remove" data-o="${esc(o.id)}" data-i="${esc(i.id)}" ` +
      `aria-label="Cancel all ${esc(i.name)}" title="Cancel this dish"><i class="bi bi-x-lg" aria-hidden="true"></i></button>`;
  } else if (i.needs_manager) {
    acts = '<span class="lo-lock" title="Already served, a manager can change this" aria-label="Manager needed"><i class="bi bi-lock" aria-hidden="true"></i></span>';
  }
  return (
    '<li class="lo-item">' +
      `<span class="lo-qty">${esc(i.quantity)}×</span>` +
      `<div class="lo-name"><div class="lo-name-main">${esc(i.name)}${comp}</div>${subs}${note}</div>` +
      `<span class="lo-st st-${esc(i.status)}">${esc(STATUS_LABEL[i.status] || i.status)}</span>` +
      (acts ? `<div class="lo-acts">${acts}</div>` : "") +
    "</li>"
  );
}

export function ticketHtml(o) {
  const attention = needsAttention(o);
  const cls = "lo-ticket" + (o.status === "billing" ? " is-billing" : attention ? " is-attention" : "");
  const meta = [esc(KIND_LABEL[o.kind] || o.source_display), o.staff ? esc(o.staff) : "", "#" + esc(o.id)].filter(Boolean).join(" · ");
  const pill = o.status === "billing" ? '<span class="lo-pill is-billing">Bill printed</span>' : "";
  const items = o.items.length
    ? '<ul class="lo-items">' + o.items.map((i) => itemRow(o, i)).join("") + "</ul>"
    : '<ul class="lo-items"><li class="lo-item"><span class="lo-name-note">No dishes on this order yet.</span></li></ul>';
  let links = "";
  if (o.add_url) links += `<a href="${esc(o.add_url)}"><i class="bi bi-plus-lg" aria-hidden="true"></i>Add</a>`;
  if (o.detail_url) links += `<a href="${esc(o.detail_url)}">Details</a>`;
  links += `<a class="is-primary" href="${esc(o.bill_url)}">Bill</a>`;
  return (
    `<article class="${cls}" aria-label="${esc(o.label)}">` +
      `<div class="lo-head"><div><h2 class="lo-title">${esc(o.label)}</h2><div class="lo-meta">${meta}</div>${pill}</div>` +
      `<div class="lo-age ${ageClass(o.age_minutes)}" title="Time since the order was opened">${ageText(o.age_minutes)}</div></div>` +
      items +
      `<div class="lo-foot"><span class="lo-total">${rupees(o.total)}</span><div class="lo-links">${links}</div></div>` +
    "</article>"
  );
}

export function boardHtml(orders, filter, query) {
  if (!orders.length) return '<div class="lo-empty"><i class="bi bi-cup-hot" aria-hidden="true"></i>No open orders right now.</div>';
  const shown = orders.filter((o) => matches(o, filter, query));
  if (!shown.length) return '<div class="lo-empty"><i class="bi bi-search" aria-hidden="true"></i>Nothing matches this filter.</div>';
  return shown.map(ticketHtml).join("");
}

export function syncText(secondsAgo) {
  return secondsAgo < 5 ? "Live" : `Updated ${secondsAgo}s ago`;
}
