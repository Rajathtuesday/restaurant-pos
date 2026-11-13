/*
 * The "Active" panel: what is already on the order the screen is looking
 * at. Dine-in looks by table (the open or billing order on it); the counter
 * looks by order id. Polled every 5 seconds; a slow answer that arrives
 * after a newer one is thrown away, and a hung one gives up after 12
 * seconds and offers a retry, so the panel never spins forever.
 */
import { rupees } from "../core/money.js";
import { esc } from "../order_edit.js";

const STATUS_LABELS = { review: "Needs Approval" };

function statusClass(status) {
  if (status === "ready") return "status-ready";
  if (status === "preparing") return "border-warning text-warning";
  if (status === "review") return "border-danger text-danger";
  return "";
}

function modifiersLine(item) {
  if (!item.modifiers || !item.modifiers.length) return "";
  return `<div class="running-mods">+ ${esc(item.modifiers.join(", "))}</div>`;
}

/* Dine-in: the dishes on the table's order and a way to cancel it. */
export function renderTableOrder(d) {
  if (!d.items || !d.items.length) {
    return '<div class="panel-note">No active services</div>';
  }
  let html = d.items.map((i) => `
    <div class="running-row d-flex flex-column align-items-start">
      <div class="d-flex justify-content-between w-100">
        <span class="running-name">${esc(i.quantity)} × ${esc(i.name || "Item")}</span>
        <span class="status-badge ${statusClass(i.status)}">${esc(i.status)}</span>
      </div>
      ${modifiersLine(i)}
    </div>`).join("");
  if (d.order_id) {
    html += `<div class="mt-4"><button type="button" class="btn-luxury w-100 border-danger text-danger" data-action="order.cancel" data-id="${d.order_id}"><i class="bi bi-x-circle me-1"></i> Cancel Entire Order</button></div>`;
  }
  return html;
}

/* The counter: items with per-item approve or reject for guest QR orders,
   Checkout once everything is approved, Cancel while the order is open. */
export function renderTokenOrder(d) {
  const status = d.order_status || "";
  if (!d.items || !d.items.length) {
    if (status === "paid" || status === "closed") {
      return '<div class="panel-note panel-note--paid"><i class="bi bi-check-circle-fill d-block mb-2"></i>Paid</div>';
    }
    return '<div class="panel-note">No active items</div>';
  }
  let hasReview = false;
  let hasBillable = false;
  let html = d.items.map((i) => {
    if (i.status === "review") hasReview = true;
    else hasBillable = true;
    const statusCell = i.status === "review"
      ? `<div class="review-item-actions">
          <button type="button" class="review-action-btn approve" title="Approve" data-action="item.approve" data-id="${i.id}"><i class="bi bi-check-lg"></i></button>
          <button type="button" class="review-action-btn reject" title="Reject" data-action="item.reject" data-id="${i.id}"><i class="bi bi-x-lg"></i></button>
        </div>`
      : `<span class="status-badge ${statusClass(i.status)}">${esc(STATUS_LABELS[i.status] || i.status)}</span>`;
    return `<div class="running-row d-flex flex-column">
      <div class="d-flex justify-content-between align-items-center w-100 running-line">
        <span class="running-name">${esc(i.quantity)} × ${esc(i.name)}</span>
        <span class="running-total">${esc(rupees(i.total))}</span>
        ${statusCell}
      </div>
      ${modifiersLine(i)}
    </div>`;
  }).join("");
  if (d.order_id && status === "open") {
    if (hasBillable && !hasReview) {
      html += `<div class="mt-3"><button type="button" class="btn-checkout w-100 btn-checkout--short" data-action="order.bill" data-id="${d.order_id}"><i class="bi bi-arrow-right-circle me-2"></i>Checkout</button></div>`;
    }
    html += `<div class="mt-2"><button type="button" class="btn-luxury w-100 btn-danger-outline" data-action="order.cancel" data-id="${d.order_id}"><i class="bi bi-x-circle me-1"></i> Cancel Order</button></div>`;
  }
  return html;
}

export const LOADING_HTML = '<div class="panel-note"><span class="spinner-border spinner-border-sm me-2"></span>Loading order…</div>';
export const ERROR_HTML = '<div class="panel-note panel-note--error">Couldn\'t load this order.<br><button type="button" class="review-action-btn approve retry-btn" data-action="running.retry">Retry</button></div>';
export const SELECT_TABLE_HTML = '<div class="panel-note">Select table</div>';

export function createRunningOrder({
  url,
  containers,                 // the elements to write the panel into
  fetchFn = (...a) => fetch(...a),
  render,                     // (data) => html
  onData = () => {},          // (data) => void: the screen updates its state from it
  timeoutMs = 12000,
  intervalMs = 5000,
}) {
  let requestId = 0;
  let timer = null;
  let query = null;           // { table } or { order }

  function write(html) {
    containers.forEach((el) => {
      if (el) el.innerHTML = html;
    });
  }

  async function load() {
    if (!query) return null;
    const mine = ++requestId;
    const controller = new AbortController();
    const abortTimer = setTimeout(() => controller.abort(), timeoutMs);
    const params = query.table ? "?table=" + encodeURIComponent(query.table) : "?order=" + encodeURIComponent(query.order);
    try {
      const r = await fetchFn(url + params, { signal: controller.signal });
      const d = await r.json();
      clearTimeout(abortTimer);
      if (mine !== requestId) return null;   // a newer request answered first
      onData(d);
      write(render(d));
      return d;
    } catch (gone) {
      clearTimeout(abortTimer);
      if (mine !== requestId) return null;
      write(ERROR_HTML);
      return null;
    }
  }

  return {
    get query() {
      return query;
    },
    /* Point the panel at a table or an order and load it. */
    look(at) {
      query = at;
      if (!at || (!at.table && !at.order)) {
        requestId += 1;
        write(SELECT_TABLE_HTML);
        return Promise.resolve(null);
      }
      return load();
    },
    refresh: load,
    showLoading() {
      write(LOADING_HTML);
    },
    start() {
      if (!timer) timer = setInterval(load, intervalMs);
    },
    stop() {
      if (timer) clearInterval(timer);
      timer = null;
    },
  };
}
