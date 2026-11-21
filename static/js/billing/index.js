/*
 * The billing screen. One module, three modes, chosen by the page's data:
 *
 *   dine_in   tables, dispatch to the kitchen, bill later
 *   counter   the QSR counter with no kitchen screen or printer: pay and
 *             print the slip in one go (the old "auto KOT mode")
 *   token     the token counter: today's orders down the side, checkout
 *             to the bill, the kitchen ticket at payment
 *
 * The page gives this module everything it needs in three json_script
 * blocks: page-data (mode, outlet, addresses, whether dishes can have
 * modifiers), dish-tax and page-offers.
 * Buttons carry data-action; this is the one place that handles them.
 */
import { readTable } from "../cart_tax.js";
import { readPage } from "../offers.js";
import { configure as configureOrderEdit, cancelOrder as orderEditCancel, notAccepted } from "../order_edit.js";
import { api, ui, platform, orderQueue, paymentQueue } from "../core/app.js";
import { csrfToken } from "../core/csrf.js";
import { delegate, onReady } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { createCart } from "./cart.js";
import { createCounter } from "./counter.js";
import { createDispatch } from "./dispatch.js";
import { createMenu } from "./menu.js";
import { createParcel } from "./parcel.js";
import { createPrinter } from "./printing.js";
import { createRunningOrder, renderTableOrder, renderTokenOrder } from "./running_order.js";
import { createLargeText, createTokenSwitcher, renderOrderHeader, setupPanelResizer } from "./tokens.js";
import { createScreen } from "./ui.js";
import { withId } from "./urls.js";

const data = pageData("page-data");
const mode = data.mode;                  // "dine_in" | "counter" | "token"
const urls = data.urls;
const outlet = data.outlet;
const csrf = () => csrfToken();

// Whatever empties or fills the cart (a tap, a sent order, a payment, an
// order saved offline), the screen redraws from it.
const cart = createCart({ onChange: () => render() });
const screen = createScreen({ mode });
const menu = createMenu({ modifiersUrl: data.modifiers ? urls.item_modifiers : null });
const printer = createPrinter({
  platform, urls, csrf, stationPrinterIp: data.station_printer_ip || "", outletId: outlet.id,
});

let dishTax = {};
let pageOffers = null;
let currentOrderId = mode === "token" ? data.order_id : null;
let orderHasItems = false;     // the order on the server, not the cart

const isMobile = () => window.innerWidth < 992;
const field = (base) => document.getElementById(base + (isMobile() ? "Mobile" : "Desktop"));
const currentSource = () => (mode === "token" ? "counter" : (field("sourceSelect")?.value || "dine_in"));
const currentTable = () => document.getElementById("tableSelectDesktop")?.value || "";

function render() {
  const parcelCharge = parcel.chargeFor(cart.items);
  const { applied, totals } = cart.totals({
    dishTax, pageOffers, outlet,
    source: currentSource(),
    operatorSources: data.operator_sources || [],
    parcel: parcelCharge,
  });
  screen.renderCart(cart, applied);
  screen.renderParcel(parcel, parcelCharge);
  screen.renderTotals(totals, { gstInclusive: !!outlet.inclusive });
  if (mode === "token") menu.syncBadges(cart);
}

const parcel = createParcel({
  rate: outlet.parcelCharge, perItem: !!outlet.parcelPerItem, api, toggleUrl: urls.toggle_parcel,
  onChange: () => render(),
});

const running = createRunningOrder({
  url: urls.running_order_items,
  containers: [document.getElementById("runningItemsDesktop"), document.getElementById("runningItemsMobile")],
  render: mode === "token" ? renderTokenOrder : renderTableOrder,
  onData: (d) => {
    orderHasItems = !!(d.items && d.items.length);
    if (mode === "token") {
      renderOrderHeader(d, currentOrderId, { billUrl: urls.bill_view });
      // A token exists before anything is on it, and the server charges a
      // parcel by the items: on an empty token the charge is a wish, kept
      // here and applied at Checkout. Otherwise the server's word shows.
      if (orderHasItems || !parcel.on) parcel.syncFrom(d);
    } else {
      currentOrderId = d.order_id || null;
      parcel.syncFrom(d);
    }
  },
});

const dispatch = createDispatch({ cart, parcel, urls, csrf, ui, screen, offlineQueue: orderQueue });
const counter = mode === "counter"
  ? createCounter({ cart, parcel, urls, csrf, ui, screen, printer, offlinePaymentQueue: paymentQueue })
  : null;
const largeText = mode === "token" ? createLargeText({}) : null;
const tokens = mode === "token"
  ? createTokenSwitcher({
    getOrderId: () => currentOrderId,
    setOrderId: (id) => { currentOrderId = id; },
    onSwitch: (id) => {
      screen.switchTab("cart");
      cart.clear();
      orderHasItems = false;
      parcel.syncFrom(null);            // a wish stays with the token it was made on
      running.showLoading();
      running.look({ order: id });
    },
  })
  : null;

configureOrderEdit({ cancelOrder: urls.cancel_order, refuse: urls.refuse_item });

/* ---- dine-in: table and source ----------------------------------------- */

function syncSource(value, view) {
  const input = document.getElementById(view === "desktop" ? "aggregatorIdDesktop" : "aggregatorIdMobile");
  if (input) {
    const aggregator = ["zomato", "swiggy", "uber_eats", "web"].includes(value);
    input.classList.toggle("d-none", !aggregator);
    if (!aggregator) input.value = "";
  }
  screen.updateOrderSummary(view);
  render();                         // GST on or off with the source
}

function syncTable(value) {
  document.querySelectorAll(".table-syncer").forEach((s) => { s.value = value; });
  const source = value ? "dine_in" : "takeaway";
  ["sourceSelectDesktop", "sourceSelectMobile"].forEach((id) => {
    const s = document.getElementById(id);
    if (s) s.value = source;
  });
  syncSource(source, "desktop");
  syncSource(source, "mobile");
  running.look(value ? { table: value } : null);
}

/* ---- the actions ------------------------------------------------------- */

async function cancelOrder(orderId) {
  const done = await orderEditCancel(orderId);
  if (done) running.refresh();
}

async function itemAction(url, itemId, okText, failText) {
  try {
    const d = await (await fetch(withId(url, itemId), { method: "POST", headers: { "X-CSRFToken": csrf() } })).json();
    if (d.success) {
      ui.toast(okText, "success");
      running.refresh();
    } else {
      ui.toast(d.error || failText, "error");
    }
  } catch (gone) {
    ui.toast(failText, "error");
  }
}

async function printReceipt(orderId) {
  try {
    const r = await printer.printReceipt(orderId);
    if (r.queued) ui.toast("Sent to printer", "success");
    else if (r.blocked) ui.toast('Popup blocked. <a href="' + r.url + '" target="_blank" rel="noopener">Open the receipt</a>', "warning");
  } catch (e) {
    ui.toast(e.message === "Queue failed" ? "Could not reach printer" : (e.message || "Network error sending to printer"), "error");
  }
}

delegate(document.body, {
  "menu.category": (el) => menu.scrollToCategory(el.dataset.target, el),
  "menu.pick": (el) => {
    const dish = menu.dishFrom(el);
    if (mode === "token") {
      cart.add(dish, { mergeBy: "dish" });
      menu.flashCard(dish.id);
    } else {
      menu.openSheet(dish);
    }
  },
  "menu.add": () => {
    const dish = menu.readSheet();
    if (!dish) return;
    cart.add(dish);
    menu.closeSheet();
  },
  "cart.inc": (el) => cart.inc(Number(el.dataset.index)),
  "cart.dec": (el) => cart.dec(Number(el.dataset.index)),
  "cart.clear": () => cart.clear(),
  "tab": (el) => screen.switchTab(el.dataset.tab),
  "mobile.open": () => screen.openMobileCart(),
  "mobile.close": () => screen.closeMobileCart(),
  "settings.toggle": (el) => screen.toggleSettings(el.dataset.view),
  "parcel.toggle": () => {
    if (mode === "token") return parcel.toggle(orderHasItems ? currentOrderId : null);
    return parcel.toggle(counter ? counter.pendingOrderId || currentOrderId : currentOrderId);
  },
  "order.submit": async () => {
    if (mode === "counter") return counter.start({ source: currentSource() });
    if (mode === "token") {
      const phone = (document.getElementById("loyaltyPhone")?.value || document.getElementById("loyaltyPhoneMobile")?.value || "").trim();
      return dispatch.checkout({ orderId: currentOrderId, customerPhone: phone });
    }
    const result = await dispatch.dispatch({
      tableId: currentTable(), source: currentSource(),
      aggregatorId: field("aggregatorId")?.value || "", orderId: currentOrderId,
    });
    if (result && result.orderId) {
      screen.closeMobileCart();
      if (result.billNow) dispatch.bill(result.orderId);
      else running.refresh();
    }
  },
  "order.bill": (el) => dispatch.bill(el.dataset.id ? Number(el.dataset.id) : currentOrderId),
  "order.cancel": (el) => cancelOrder(Number(el.dataset.id)),
  "item.approve": (el) => itemAction(urls.approve_item, el.dataset.id, "Item approved", "Could not approve item"),
  // Not accepting a guest's dish asks why; the guest's phone shows it.
  "item.reject": async (el) => {
    const done = await notAccepted({ id: el.dataset.id, name: el.dataset.name || "this dish", quantity: Number(el.dataset.quantity) || 1, status: "review" });
    if (done) running.refresh();
  },
  "receipt.print": (el) => printReceipt(Number(el.dataset.id)),
  "token.select": (el, event) => {
    event.preventDefault();
    if (tokens.select(Number(el.dataset.id), el.getAttribute("href")) === "same") screen.switchTab("cart");
  },
  "text.toggle": () => largeText.toggle(),
  "running.retry": () => running.refresh(),
  "qsr.method": (el) => counter.selectMethod(el),
  "qsr.pay": () => counter.pay(),
  "qsr.cancel": () => counter.cancel(),
  "banner.close": (el) => { const b = el.closest("[data-banner]"); if (b) b.style.display = "none"; },
});

delegate(document.body, {
  "table.sync": (el) => syncTable(el.value),
  "source.change": (el) => syncSource(el.value, el.dataset.view),
  "modifier.limit": (el) => menu.enforceLimit(el),
}, "change");

delegate(document.body, {
  "menu.search": (el) => menu.filter(el.value),
  "qsr.amount": () => counter.updateChange(),
}, "input");

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !screen.mobileCartOpen()) return;
  if (document.querySelector(".modal.show")) return;
  screen.closeMobileCart();
});

/* ---- the kitchen printer's health (dine-in and counter) ---------------- */

async function checkPrinter() {
  try {
    const d = await (await fetch(urls.printer_status)).json();
    if (d.error) screen.showPrinterError(d.message);
  } catch (quiet) { /* the next check tells */ }
}

/* ---- go ----------------------------------------------------------------- */

onReady(() => {
  dishTax = readTable("dish-tax");
  pageOffers = readPage("page-offers");
  render();
  menu.activateFirstCategory();

  if (mode === "token") {
    largeText.init();
    setupPanelResizer({});
    running.showLoading();
    running.look({ order: currentOrderId });
  } else {
    screen.updateOrderSummary("desktop");
    screen.updateOrderSummary("mobile");
    running.look(currentTable() ? { table: currentTable() } : null);
    checkPrinter();
    setInterval(checkPrinter, 20000);
  }
  running.start();
});
