/*
 * The live orders board (orders/live_orders.html): every open order at the
 * outlet, refreshed every 8 seconds, with filters, a search, and remove
 * one or cancel a dish (order_edit.js's sheets). The addresses come from
 * page-data (orders.views.live_orders.live_orders_page_data).
 *
 * The board is redrawn only when its markup changed, and a refresh waits
 * while a sheet is open, a change is saving or the tab is hidden, so
 * nothing moves under the person's finger.
 */
import { delegate, onReady, qsa } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import OrderEdit from "../order_edit.js";
import { boardHtml, counts, syncText } from "./board.js";
import { createOrdersLoader } from "./loader.js";

const data = pageData("page-data");
const grid = document.getElementById("lo-grid");
const errorBox = document.getElementById("lo-error");
const sync = document.getElementById("lo-sync");
const syncLine = document.getElementById("lo-sync-text");

OrderEdit.configure({ reduce: data.urls.reduce, cancel: data.urls.cancel, cancelOrder: data.urls.cancel_order });

let orders = [];
let filter = "all";
let query = "";
let lastOk = 0;
let busy = false;
let drawn = null;

function draw() {
  const c = counts(orders);
  for (const el of qsa(".lo-count")) {
    const n = c[el.dataset.c] || 0;
    el.textContent = n;
    el.classList.toggle("has", n > 0);
  }
  const html = boardHtml(orders, filter, query);
  if (html === drawn) return;
  drawn = html;
  grid.innerHTML = html;
}

function tickSync() {
  if (!lastOk) return;
  const secs = Math.round((Date.now() - lastOk) / 1000);
  syncLine.textContent = syncText(secs);
  sync.classList.toggle("is-stale", secs > 30);
}

const loader = createOrdersLoader({
  fetchFn: (...args) => fetch(...args),
  url: data.urls.data,
  onOrders: (d) => {
    orders = d.orders;
    const s = d.summary || {};
    document.getElementById("st-open").textContent = s.open_orders ?? 0;
    document.getElementById("st-kitchen").textContent = s.in_kitchen ?? 0;
    document.getElementById("st-ready").textContent = s.ready ?? 0;
    document.getElementById("st-bill").textContent = s.awaiting_bill ?? 0;
    draw();
    lastOk = Date.now();
    errorBox.hidden = true;
    tickSync();
  },
  onFailure: (message) => {
    errorBox.textContent = `Couldn't refresh the orders. Showing the last update, trying again… (${message})`;
    errorBox.hidden = false;
    sync.classList.add("is-stale");
  },
});

function poll() {
  if (busy || document.hidden || OrderEdit.sheetOpen()) return;
  loader.refresh();
}

function findItem(button) {
  const o = orders.find((x) => String(x.id) === button.dataset.o);
  return o ? o.items.find((x) => String(x.id) === button.dataset.i) : null;
}

async function edit(button, how) {
  const item = findItem(button);
  if (!item || busy) return;
  busy = true;
  try {
    await (how === "reduce" ? OrderEdit.reduce(item) : OrderEdit.remove(item));
  } finally {
    busy = false;
    loader.refresh();
  }
}

delegate(document.body, {
  "order.reduce": (b) => edit(b, "reduce"),
  "order.remove": (b) => edit(b, "remove"),
  "lo.filter": (b) => {
    filter = b.dataset.f;
    for (const x of qsa(".lo-filter")) x.setAttribute("aria-pressed", x === b ? "true" : "false");
    draw();
  },
});
delegate(document.body, {
  "lo.search": (input) => {
    query = input.value.trim().toLowerCase();
    draw();
  },
}, "input");

document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

onReady(() => {
  loader.refresh();
  setInterval(poll, data.poll_ms || 8000);
  setInterval(tickSync, 1000);
});
