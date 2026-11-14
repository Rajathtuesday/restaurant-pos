/*
 * What the running order page draws: one card per dish, the buttons this
 * person may use on it, and the line that says a refresh failed. Every
 * value from the server is escaped. Pure functions, tested in node.
 *
 * `can` comes from the server (orders.views.order_views.running_order_page_data):
 * edit, serve, comp, edit_served.
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

export function actionsHtml(item, can) {
  if (item.status === "voided") return "";
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
  const reason = voided && item.void_reason ? `<div class="void-reason">Cancelled: ${esc(item.void_reason)}</div>` : "";
  return `<div class="item-card${voided ? " is-voided" : ""}" data-item="${esc(item.id)}">
    <div>
      <div class="item-name">${esc(item.name)}</div>
      ${modifiers}
      <div class="item-meta">
        <span>Qty: ${esc(item.quantity)}</span>
        <span class="status-badge ${badgeClass(item.status)}">${esc(voided ? "cancelled" : item.status)}</span>${free}
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
  return sortItems(items).map((item) => itemHtml(item, can)).join("");
}
