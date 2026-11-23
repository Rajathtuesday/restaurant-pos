/*
 * The kitchen display's tickets, as markup: one card per KOT, its dishes,
 * and the button each dish needs next (Start Prep, then Ready, then Done).
 *
 * Dish names, notes and table names are typed by staff and guests, so
 * every one is escaped: without it one person's note would run as script
 * on every kitchen screen. Nothing here touches the page; index.js puts
 * the markup in.
 */
import { esc } from "../order_edit.js";

/* Whole minutes since a ticket was sent; 0 for a time that cannot be read. */
export function minutesSince(createdAt, now = Date.now()) {
  const created = createdAt ? new Date(createdAt).getTime() : NaN;   // new Date(null) is 1970
  if (Number.isNaN(created)) return 0;
  return Math.max(0, Math.floor((now - created) / 60000));
}

/* Gold under 15 minutes, amber under 25, red and pulsing after. */
export function colorClass(minutes) {
  if (minutes < 15) return "card-fresh";
  if (minutes < 25) return "card-warn";
  return "card-urgent";
}

export const allDone = (kot) => kot.items.every((i) => i.status === "ready" || i.status === "served");

function nextStep(item) {
  const id = Number(item.id);
  if (item.status === "sent") {
    return `<button type="button" class="btn-status btn-start" data-action="item.start" data-id="${id}">Start Prep</button>`;
  }
  if (item.status === "preparing") {
    return `<button type="button" class="btn-status btn-ready" data-action="item.ready" data-id="${id}">Ready ✓</button>`;
  }
  return '<span class="badge-done">Done</span>';
}

function itemRow(item) {
  const mods = (item.modifiers || []).map((m) => `<span class="item-note item-mod">+ ${esc(m)}</span>`).join("");
  const note = mods + (item.notes ? `<span class="item-note">${esc(item.notes)}</span>` : "");
  return `<div class="item-row" data-item="${Number(item.id)}">
    <div class="item-main">
      <span class="item-qty">${esc(item.quantity)}</span>
      <div class="item-text"><span class="item-name">${esc(item.name)}</span>${note}</div>
    </div>
    <div class="action-cell">${nextStep(item)}</div>
  </div>`;
}

function card(kot, now) {
  const minutes = minutesSince(kot.created_at, now);
  const bump = allDone(kot) ? "" :
    `<button type="button" class="btn-bump" data-action="kot.bump" data-id="${Number(kot.id)}"><i class="bi bi-check2-all"></i>BUMP: All Ready</button>`;
  return `<div class="col-12 col-md-6 col-xl-4 col-xxl-3">
    <div class="order-card ${colorClass(minutes)}" data-kot="${Number(kot.id)}">
      <div class="order-header">
        <div>
          <div class="table-name">${esc(kot.table)}</div>
          <div class="kot-line"><span class="kot-num">KOT #${esc(kot.kot_number)}</span></div>
        </div>
        <div class="timer">${minutes}m</div>
      </div>
      <div class="items-container">${kot.items.map(itemRow).join("")}</div>
      ${bump}
    </div>
  </div>`;
}

export function emptyHtml() {
  return `<div class="empty-state">
    <i class="bi bi-check2-all is-ok"></i>
    <h3>All Caught Up</h3>
    <p class="state-note">No active orders.</p>
  </div>`;
}

export function failedHtml(message) {
  return `<div class="empty-state" data-failed>
    <i class="bi bi-wifi-off is-failed"></i>
    <h3 class="is-failed">Sync Failed</h3>
    <p class="state-note">${esc(message)} Retrying…</p>
  </div>`;
}

/* The whole board: the tickets, or "All Caught Up" when there are none. */
export function boardHtml(kots, now = Date.now()) {
  if (!kots.length) return emptyHtml();
  return kots.map((kot) => card(kot, now)).join("");
}
