/*
 * The guest QR menu: browse, filter, pick options, the cart, placing the
 * order (and a second round on the same bill), the order's status, calling
 * a waiter. The page gives this module its addresses and the outlet's tax
 * rules in one block, page-data (menu.views.customer_views.qr_menu_page_data);
 * every button carries data-action and is handled here.
 *
 * No browser pop-ups: alert() and confirm() look like a website from 2005
 * and show the server's address. Messages go to the page's own toast; a
 * missing required option is said inside the options sheet.
 */
import { readTable, line as taxLine, totals as taxTotals } from "../cart_tax.js";
import { delegate, onReady, qsa } from "../core/dom.js";
import { rupees } from "../core/money.js";
import { pageData } from "../core/page_data.js";
import { note as offerNote, onCart, readPage } from "../offers.js";
import { createGuestCart } from "./cart.js";
import { createModifierPick } from "./modifiers.js";
import { createOrderSender, createOrderStore } from "./order.js";
import { createStatusPoller } from "./status.js";
import { cartHtml, modifierGroupsHtml, statusItemsHtml } from "./view.js";

const data = pageData("page-data");
const optionsByDish = pageData("item-modifiers-raw");
const dishTax = readTable("dish-tax");
const pageOffers = readPage("page-offers");
const el = (id) => document.getElementById(id);

const cart = createGuestCart();
let storage = null;
try {
  storage = window.localStorage;
} catch (blocked) {
  storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
}
const store = createOrderStore(storage, data.table_token);
const sender = createOrderSender({
  fetchFn: (...args) => fetch(...args), url: data.urls.create_order, csrf: data.csrf, store,
});
let alreadyOrdered = [];

/* ---- the toast ------------------------------------------------------------ */

let toastTimer = null;
function toast(words, isError = false) {
  const box = el("miniToast");
  clearTimeout(toastTimer);
  box.innerHTML = "";
  const icon = document.createElement("i");
  icon.className = "bi " + (isError ? "bi-exclamation-circle" : "bi-check-circle");
  box.append(icon, " " + words);
  box.classList.toggle("error", isError);
  box.classList.add("show");
  toastTimer = setTimeout(() => box.classList.remove("show"), 3200);
}

/* ---- drawing -------------------------------------------------------------- */

function drawDish(id) {
  const qty = cart.qty(id);
  el(`addBtn-${id}`)?.classList.toggle("hidden", qty > 0);
  el(`qtyCtrl-${id}`)?.classList.toggle("active", qty > 0);
  const shown = el(`qty-${id}`);
  if (shown) shown.textContent = String(qty || 1);
}

function cartOpen() {
  return el("cartModal").classList.contains("active");
}

function drawCart() {
  const lines = cart.lines();
  const taxed = lines.map((l) => taxLine(cart.lineTotal(l), dishTax, l.id, l.gstRate));
  const applied = onCart(pageOffers, taxed, lines.map((l) => ({ dishId: l.id, quantity: l.qty })));
  el("cartItemsContainer").innerHTML = cartHtml({
    // onCart answers {} with no offers and a list with them: read it by index.
    lines, lineTotal: cart.lineTotal, notes: lines.map((l, i) => offerNote(applied[i])), already: alreadyOrdered,
  });
  const t = taxTotals(taxed, data.outlet);
  el("billSubtotal").textContent = rupees(t.subtotal);
  el("billOffers").textContent = rupees(t.offers);
  el("billOffersRow").hidden = !(t.offers > 0);
  const gst = el("billGst");
  if (gst) gst.textContent = rupees(t.gst);
  el("billVat").textContent = rupees(t.vat);
  el("billVatRow").hidden = !(t.vat > 0);
  el("billTotal").textContent = rupees(t.total);
}

function drawBar() {
  const count = cart.count();
  el("totalItems").textContent = String(count);
  el("totalPrice").textContent = rupees(cart.total());
  el("cartBar").classList.toggle("visible", count > 0);
  const eta = cart.eta();
  el("etaText").textContent = eta ? `${eta.low} to ${eta.high} min` : "-";
  el("orderStatusPill").classList.toggle("above-cart-bar", count > 0);
  if (!count) closeCart();
  if (cartOpen()) drawCart();
}

function changed(id) {
  if (id !== undefined) drawDish(id);
  drawBar();
}

/* ---- sheets --------------------------------------------------------------- */

const show = (overlay, sheet, on) => {
  el(overlay).classList.toggle("active", on);
  el(sheet).classList.toggle("active", on);
};
function openCart() {
  drawCart();
  show("cartOverlay", "cartModal", true);
}
function closeCart() {
  show("cartOverlay", "cartModal", false);
}

let pick = null;
let pickDish = null;
function openOptions(dish, groups) {
  pickDish = dish;
  pick = createModifierPick(groups, dish.price);
  el("modifierItemName").textContent = dish.name;
  el("modifierGroupsContainer").innerHTML = modifierGroupsHtml(groups);
  el("modifierError").textContent = "";
  el("modTotalAmt").textContent = rupees(pick.total());
  show("modifierOverlay", "modifierModal", true);
}
function closeOptions() {
  show("modifierOverlay", "modifierModal", false);
  pick = null;
  pickDish = null;
}

/* ---- the order's status --------------------------------------------------- */

function hideStatus() {
  el("orderStatusPill").classList.add("hidden");
  show("statusSheetOverlay", "statusSheet", false);
  alreadyOrdered = [];
  if (cartOpen()) drawCart();
}

const poller = createStatusPoller({
  fetchFn: (...args) => fetch(...args), url: data.urls.order_status, store, every: data.poll_ms || 6000,
  onGone: hideStatus,
  onStatus: (s) => {
    el("orderStatusPill").classList.remove("hidden");
    el("orderStatusPill").classList.toggle("above-cart-bar", cart.count() > 0);
    const number = el("orderStatusNumber");
    number.textContent = s.token || "";
    number.classList.toggle("hidden", !s.token);
    el("orderStatusDot").classList.toggle("done", s.done);
    el("orderStatusPillLabel").textContent = s.pill;
    el("statusSheetLabel").textContent = s.sheet;
    el("statusSheetTable").textContent = s.table;
    qsa("#statusStages .status-stage").forEach((stage) => {
      const at = Number(stage.dataset.stage);
      stage.classList.toggle("done", at < s.stage);
      stage.classList.toggle("active", at === s.stage);
    });
    alreadyOrdered = s.items;
    el("statusSheetItems").innerHTML = statusItemsHtml(s.items);
    if (cartOpen()) drawCart();
  },
});

/* ---- placing the order ---------------------------------------------------- */

const PLACE = el("placeOrderBtn").innerHTML;

async function placeOrder(button) {
  if (!cart.count()) return;
  if (!data.table_token && !data.table_id) {
    toast("Scan a table's QR code to order.", true);
    return;
  }
  button.disabled = true;
  button.textContent = "Sending your order...";
  const result = await sender.place({
    cart: cart.payload(), tableId: data.table_id, tableToken: data.table_token,
    notes: el("specialInstructions").value,
  });
  button.disabled = false;
  button.innerHTML = PLACE;
  if (result.kind === "placed") {
    const ids = cart.lines().map((l) => l.id);
    cart.clear();
    ids.forEach(drawDish);
    el("specialInstructions").value = "";
    drawBar();
    closeCart();
    toast("Order sent to the kitchen");
    poller.start();
  } else if (result.kind === "not-yours") {
    toast("Starting a new order for this table: tap Place Order again.", true);
  } else if (result.kind === "billed") {
    toast("This table's bill is already out: please ask a waiter for more.", true);
  } else if (result.kind === "refused") {
    toast(result.message, true);
  } else if (result.kind === "network") {
    toast("No connection: tap Place Order again. Nothing will be ordered twice.", true);
  }
}

/* ---- calling a waiter ----------------------------------------------------- */

function waiterButton(label, { notified = false, icon = "bi bi-bell" } = {}) {
  const button = el("callWaiterBtn");
  el("callWaiterLabel").textContent = label;
  button.classList.toggle("notified", notified);
  button.querySelector("i").className = icon;
}

async function callWaiter(button) {
  if (!data.urls.call_waiter || button.disabled) return;
  button.disabled = true;
  button.classList.add("ringing");
  waiterButton("Calling...");
  try {
    const answer = await (await fetch(data.urls.call_waiter, { method: "POST", headers: { "X-CSRFToken": data.csrf } })).json();
    button.classList.remove("ringing");
    if (answer.success) {
      waiterButton("Notified", { notified: true, icon: "bi bi-check-circle-fill" });
      toast("A waiter has been notified");
      setTimeout(() => { button.disabled = false; waiterButton("Call Waiter"); }, 60000);
      return;
    }
    toast(answer.error || "Could not call a waiter", true);
  } catch (noAnswer) {
    button.classList.remove("ringing");
    toast("No connection: please try again", true);
  }
  waiterButton("Call Waiter");
  button.disabled = false;
}

/* ---- browsing ------------------------------------------------------------- */

let search = "";
let diet = "all";
function filter() {
  qsa(".item-card").forEach((card) => {
    const hit = card.dataset.search.includes(search) && (diet === "all" || card.dataset.diet === diet);
    card.classList.toggle("is-filtered", !hit);
  });
}

const dishOf = (card) => ({
  id: card.dataset.id, name: card.dataset.name, price: Number(card.dataset.price),
  prepTime: Number(card.dataset.prep), gstRate: Number(card.dataset.gst),
});

let clearing = null;

delegate(document.body, {
  "dish.add": (button) => {
    const dish = dishOf(button.closest(".item-card"));
    const groups = optionsByDish[String(dish.id)] || [];
    if (groups.length && !cart.has(dish.id)) {
      openOptions(dish, groups);
      return;
    }
    cart.add(dish);
    changed(dish.id);
  },
  "dish.inc": (button) => { cart.inc(button.dataset.id); changed(button.dataset.id); },
  "dish.dec": (button) => { cart.dec(button.dataset.id); changed(button.dataset.id); },
  "cart.inc": (button) => { cart.inc(button.dataset.id); changed(button.dataset.id); },
  "cart.dec": (button) => { cart.dec(button.dataset.id); changed(button.dataset.id); },
  "cart.open": () => openCart(),
  "cart.close": () => closeCart(),
  "cart.clear": (button) => {
    if (!clearing) {
      button.classList.add("is-asking");
      button.setAttribute("aria-label", "Tap again to clear the cart");
      el("clearCartLabel").textContent = "Clear?";
      clearing = setTimeout(() => {
        clearing = null;
        button.classList.remove("is-asking");
        el("clearCartLabel").textContent = "";
      }, 3000);
      return;
    }
    clearTimeout(clearing);
    clearing = null;
    button.classList.remove("is-asking");
    el("clearCartLabel").textContent = "";
    const ids = cart.lines().map((l) => l.id);
    cart.clear();
    ids.forEach(drawDish);
    drawBar();
  },
  "order.place": (button) => placeOrder(button),
  "modifier.toggle": (button) => {
    if (!pick) return;
    pick.toggle(button.dataset.group, button.dataset.id);
    qsa("#modifierGroupsContainer [data-check]").forEach((check) => {
      check.classList.toggle("checked", pick.isPicked(check.dataset.check));
    });
    el("modifierError").textContent = "";
    el("modTotalAmt").textContent = rupees(pick.total());
  },
  "modifier.confirm": () => {
    if (!pick) return;
    const missing = pick.missing();
    if (missing) {
      el("modifierError").textContent = `Please choose an option for "${missing}".`;
      return;
    }
    cart.add(pickDish, pick.modifiers());
    const id = pickDish.id;
    closeOptions();
    changed(id);
  },
  "modifier.close": () => closeOptions(),
  "status.open": () => show("statusSheetOverlay", "statusSheet", true),
  "status.close": () => show("statusSheetOverlay", "statusSheet", false),
  "waiter.call": (button) => callWaiter(button),
  "diet.filter": (button) => {
    qsa(".diet-filter").forEach((b) => b.classList.toggle("active", b === button));
    diet = button.dataset.filter;
    filter();
  },
  "theme.toggle": () => {
    const dark = document.documentElement.classList.toggle("dark");
    try {
      storage.setItem("dark", String(dark));
    } catch (blocked) { /* this visit only */ }
    el("themeIcon").className = dark ? "bi bi-sun" : "bi bi-moon";
  },
});

// No search box when the menu has no dishes yet.
el("searchInput")?.addEventListener("input", (event) => {
  search = event.target.value.trim().toLowerCase();
  filter();
});

/* ---- the sticky bars and the category tabs --------------------------------- */

function stickyNav() {
  const header = document.querySelector(".menu-header");
  const tabs = document.querySelector(".category-tabs");
  if (!header || !tabs) return;
  const headerHeight = header.getBoundingClientRect().height;
  // The tabs sit just under the (sticky) header. Both are sticky, so they
  // already take their height in the page; only a tapped section's title
  // needs room under them.
  tabs.style.top = headerHeight + "px";
  const room = headerHeight + tabs.getBoundingClientRect().height + 16;
  qsa(".category-section").forEach((section) => { section.style.scrollMarginTop = room + "px"; });
}

function followTabs() {
  let current = "";
  qsa(".category-section").forEach((section) => {
    if (window.scrollY >= section.offsetTop - 150) current = section.id;
  });
  qsa(".cat-tab").forEach((tab) => {
    const on = tab.getAttribute("href") === `#${current}`;
    tab.classList.toggle("active", on);
    if (on) el("catTabs").scrollTo({ left: tab.offsetLeft - 20, behavior: "smooth" });
  });
}

onReady(() => {
  el("themeIcon").className = document.documentElement.classList.contains("dark") ? "bi bi-sun" : "bi bi-moon";
  stickyNav();
  window.addEventListener("resize", stickyNav);
  window.addEventListener("scroll", followTabs, { passive: true });
  poller.start();
});
