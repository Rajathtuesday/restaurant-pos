/*
 * The running order page: one order's dishes as the kitchen works on them,
 * refreshed every few seconds, with serve, remove one, cancel, comp,
 * approve or not accept a guest's QR dish, and cancel the whole order.
 *
 * The page gives this module its addresses and what this person may do in
 * one json_script block, page-data
 * (orders.views.order_views.running_order_page_data). Buttons carry
 * data-action; this is the one place that handles them.
 */
import { ui } from "../core/app.js";
import { delegate, onReady } from "../core/dom.js";
import { rupees } from "../core/money.js";
import { pageData } from "../core/page_data.js";
import OrderEdit from "../order_edit.js";
import { createRunningOrderActions } from "./actions.js";
import { createOrderLoader } from "./loader.js";
import { listHtml, moneyRowsHtml } from "./view.js";

const data = pageData("page-data");
const list = document.getElementById("items");
const status = document.getElementById("ro-status");
const totals = {
  rows: document.getElementById("moneyRows"),
  total: document.getElementById("total"),
};

OrderEdit.configure({
  reduce: data.urls.reduce,
  cancel: data.urls.cancel,
  cancelOrder: data.urls.cancel_order,
  complimentary: data.urls.comp,
  refuse: data.urls.refuse,
});

/* The dishes as last drawn, by id, for the sheets; and the markup, so the
   list is only touched when it changed (a redraw every 3 seconds would
   take the focus off a button the cashier is about to tap). */
let itemsById = {};
let drawn = null;

const loader = createOrderLoader({
  fetchFn: (...args) => fetch(...args),
  url: data.urls.data,
  onOrder: (order) => {
    status.hidden = true;
    totals.rows.innerHTML = moneyRowsHtml(order.rows);
    totals.total.textContent = rupees(order.total);
    itemsById = Object.fromEntries(order.items.map((i) => [String(i.id), i]));
    const html = listHtml(order.items, data.can);
    if (html === drawn) return;
    drawn = html;
    list.innerHTML = html;
  },
  onFailure: (message) => {
    status.textContent = "Could not refresh this order. " + message;
    status.hidden = false;
  },
});

const actions = createRunningOrderActions({
  edit: OrderEdit,
  toast: (message, icon) => ui.toast(message, icon),
  urls: data.urls,
  orderId: data.order_id,
  refresh: () => loader.refresh(),
  go: (url) => { window.location.href = url; },
});

const item = (button) => itemsById[button.dataset.id];

delegate(document.body, {
  "item.serve": (button) => actions.serve(button.dataset.id),
  "item.reduce": (button) => item(button) && actions.reduce(item(button)),
  "item.remove": (button) => item(button) && actions.remove(item(button)),
  "item.comp": (button) => item(button) && actions.comp(item(button)),
  "item.approve": (button) => actions.approve(button.dataset.id),
  "item.refuse": (button) => item(button) && actions.refuse(item(button)),
  "order.approveAll": () => actions.approveAll(),
  "order.cancel": () => actions.cancelOrder(),
});

/* A poll waits while a sheet is open or a tap is out (the action asks
   for the list itself when it is done), and while the tab is hidden. */
function poll() {
  if (actions.busy() || OrderEdit.sheetOpen() || document.hidden) return;
  loader.poll();
}

onReady(() => {
  loader.refresh();
  setInterval(poll, data.poll_ms || 3000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
});
