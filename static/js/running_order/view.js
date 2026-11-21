/*
 * What the running order page draws: one card per dish, the buttons this
 * person may use on it, and the line that says a refresh failed. Every
 * value from the server is escaped. Pure functions, tested in node.
 *
 * `can` comes from the server (orders.views.order_views.running_order_page_data):
 * edit, serve, comp, edit_served, approve.
 */
import { emptyStateHtml } from "../core/empty_state.js";
import { esc } from "../order_edit.js";

const BADGE = {
  sent: "badge-ordering",
  preparing: "badge-preparing",
  ready: "badge-ready",
  served: "badge-served",
  voided: "badge-voided",
};

export function badgeClass(status) {
  return BADGE[status] || "badge-pending";
}

/* Active dishes first, cancelled ones underneath as a record. */
export function sortItems(items) {
  return items.slice().sort((a, b) => (a.status === "voided") - (b.status === "voided"));
}

/* A guest's QR dish waiting for a yes: a tick to accept it, a cross to
   not accept it, and nothing else until it is answered. */
function reviewHtml(item, can) {
  if (!can.approve) return "";
  const id = esc(item.id);
  const name = esc(item.name);
  return `<button type="button" class="ro-guest is-yes" data-action="item.approve" data-id="${id}" ` +
      `aria-label="Approve ${name}" title="Approve"><i class="bi bi-check-lg" aria-hidden="true"></i></button>` +
    `<button type="button" class="ro-guest is-no" data-action="item.refuse" data-id="${id}" ` +
      `aria-label="Not accepted: ${name}" title="Not accepted"><i class="bi bi-x-lg" aria-hidden="true"></i></button>`;
}

export function actionsHtml(item, can) {
  if (item.status === "voided") return "";
  if (item.status === "review") return reviewHtml(item, can);
  const id = esc(item.id);
  const name = esc(item.name);
  let html = "";
  if (item.status === "ready" && can.serve) {
    html += `<button type="button" class="btn-luxury btn-gold" data-action="item.serve" data-id="${id}">` +
      `<i class="bi bi-check2-circle" aria-hidden="true"></i> Serve</button>`;
  }
  const locked = item.status === "served" && !can.edit_served;
  if (can.edit && !locked) {
    html += `<button type="button" class="btn-step" data-action="item.reduce" data-id="${id}" ` +
      `aria-label="Remove one ${name}" title="Remove one"><i class="bi bi-dash-lg" aria-hidden="true"></i></button>`;
    html += `<button type="button" class="btn-luxury ro-danger" data-action="item.remove" data-id="${id}" ` +
      `aria-label="Cancel ${name}">Cancel</button>`;
  }
  if (can.edit && locked) {
    html += `<span class="ro-lock" title="Served: a manager can change it"><i class="bi bi-lock" aria-hidden="true"></i>` +
      `<span class="visually-hidden">Served: a manager can change it</span></span>`;
  }
  if (can.comp && !item.is_complimentary) {
    html += `<button type="button" class="btn-luxury" data-action="item.comp" data-id="${id}" ` +
      `aria-label="Make ${name} complimentary" title="Make complimentary">Comp</button>`;
  }
  return html;
}

export function itemHtml(item, can) {
  const voided = item.status === "voided";
  const modifiers = item.modifiers && item.modifiers.length
    ? `<div class="ro-mods">+ ${esc(item.modifiers.join(", "))}</div>` : "";
  const free = item.is_complimentary && !voided ? ` <span class="status-badge badge-comp">Free</span>` : "";
  const said = item.not_accepted ? "Not accepted" : "Cancelled";
  const reason = voided && item.void_reason ? `<div class="void-reason">${said}: ${esc(item.void_reason)}</div>` : "";
  const shown = voided ? (item.not_accepted ? "not accepted" : "cancelled")
    : item.status === "review" ? "needs approval" : item.status;
  return `<div class="item-card${voided ? " is-voided" : ""}" data-item="${esc(item.id)}">
    <div>
      <div class="item-name">${esc(item.name)}</div>
      ${modifiers}
      <div class="item-meta">
        <span>Qty: ${esc(item.quantity)}</span>
        <span class="status-badge ${badgeClass(item.status)}">${esc(shown)}</span>${free}
      </div>
      ${reason}
    </div>
    <div class="ro-actions">${actionsHtml(item, can)}</div>
  </div>`;
}

export function listHtml(items, can) {
  if (!items.length) {
    return emptyStateHtml({
      icon: "receipt",
      title: "No dishes on this order yet",
      text: "Dishes added from billing show here as the kitchen works on them.",
    });
  }
  return approveAllHtml(items, can) + sortItems(items).map((item) => itemHtml(item, can)).join("");
}

/* With more than one guest dish waiting, one tap accepts them all. */
export function approveAllHtml(items, can) {
  const waiting = items.filter((i) => i.status === "review").length;
  if (!can.approve || waiting < 2) return "";
  return `<div class="ro-guest-bar"><span>${waiting} guest dishes waiting</span>` +
    `<button type="button" class="btn-luxury btn-gold" data-action="order.approveAll">` +
    `<i class="bi bi-check-circle" aria-hidden="true"></i> Approve all</button></div>`;
}
