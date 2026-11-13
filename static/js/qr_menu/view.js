/*
 * What the QR menu's scripts draw: the options sheet, the cart, the status
 * sheet's dishes. Markup only, every name escaped: dish and option names
 * are typed by restaurant staff and shown on a public page, so a name with
 * markup in it must never run in a guest's browser.
 */
import { rupees } from "../core/money.js";

export function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* The options sheet for one dish: each group, its options as buttons. */
export function modifierGroupsHtml(groups) {
  return groups.map((group) => {
    const badge = group.is_required
      ? `<span class="modifier-req-badge is-required">Required · pick ${esc(group.max_select)}</span>`
      : `<span class="modifier-req-badge">Optional · max ${esc(group.max_select)}</span>`;
    const options = group.modifiers.map((m) => `
      <button type="button" class="modifier-option" data-action="modifier.toggle" data-group="${esc(group.id)}" data-id="${esc(m.id)}">
        <span class="modifier-check" data-check="${esc(m.id)}"></span>
        <span>${esc(m.name)}</span>
        <span class="${Number(m.price) > 0 ? "modifier-price ms-auto" : "ms-auto"}">${Number(m.price) > 0 ? "+" + esc(rupees(m.price)) : ""}</span>
      </button>`).join("");
    return `<div class="modifier-group-section">
      <div class="modifier-group-header"><strong>${esc(group.name)}</strong>${badge}</div>
      <div class="modifier-options">${options}</div>
    </div>`;
  }).join("");
}

function orderedRows(items) {
  return items.map((i) => `<div class="status-sheet-item-row">
      <span class="name">${esc(i.quantity)}x ${esc(i.name)}</span>
      <span>${esc(i.status)}</span>
    </div>`).join("");
}

/*
 * The cart sheet's dishes. `already` is what this table has on its way
 * (shown read-only first, so a second round is ordered knowing the first);
 * `notes` the offer line for each cart line (offers.js note()).
 */
export function cartHtml({ lines, lineTotal, notes = [], already = [] }) {
  let html = "";
  if (already.length) {
    html += `<div class="cart-items-text cart-section-label">Already ordered</div>${orderedRows(already)}`;
    if (lines.length) html += '<div class="cart-items-text cart-section-label is-adding">Adding now</div>';
  }
  html += lines.map((line, i) => {
    const options = line.modifiers && line.modifiers.length
      ? `<div class="cart-modifier-names">${line.modifiers.map((m) => esc(m.name)).join(", ")}</div>` : "";
    return `<div class="cart-item-row" data-line="${esc(line.id)}">
      <div>
        <div class="cart-item-name">${esc(line.name)}</div>
        ${options}
        ${notes[i] || ""}
        <div class="qty-controls active cart-qty">
          <button type="button" class="qty-btn" data-action="cart.dec" data-id="${esc(line.id)}" aria-label="One fewer ${esc(line.name)}">−</button>
          <span class="qty-num">${esc(line.qty)}</span>
          <button type="button" class="qty-btn" data-action="cart.inc" data-id="${esc(line.id)}" aria-label="One more ${esc(line.name)}">+</button>
        </div>
      </div>
      <div class="cart-item-price">${esc(rupees(lineTotal(line)))}</div>
    </div>`;
  }).join("");
  return html;
}

export function statusItemsHtml(items) {
  return orderedRows(items) || '<div class="status-sheet-item-row"><span>No items yet</span></div>';
}
