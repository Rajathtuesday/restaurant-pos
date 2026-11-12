/*
 * What only the counter's screen has: switching between today's token
 * orders without reloading the page, the large-text mode, and the drag
 * handles between the three desktop panels.
 */
import { esc } from "../order_edit.js";
import { withId } from "./urls.js";

/* The header of the Current Order panel and its Pay or Print button. */
export function renderOrderHeader(d, orderId, { doc = document, billUrl }) {
  const el = (id) => doc.getElementById(id);
  ["orderTokenDisplay", "orderTokenDisplayMobile"].forEach((id) => {
    const e = el(id);
    if (e) e.textContent = d.token_display || "-";
  });
  const title = el("orderTokenDisplayMobileTitle");
  if (title) title.textContent = "Order " + (d.token_display || "");
  const status = el("orderStatusDisplay");
  if (status) status.textContent = d.order_status_display || "";

  let html = "";
  if (d.order_status === "billing") {
    html = `<a href="${withId(billUrl, orderId)}" class="btn-checkout btn-checkout--pay d-flex align-items-center justify-content-center text-decoration-none mb-2">
      <i class="bi bi-credit-card me-2"></i>Pay Now · ₹${esc(Math.round(d.remaining || 0))}</a>`;
  } else if (d.order_status === "paid" || d.order_status === "closed") {
    html = `<button type="button" class="btn-luxury w-100 mb-2 btn-tall" data-action="receipt.print" data-id="${orderId}">
      <i class="bi bi-printer me-2"></i>Print Receipt</button>`;
  }
  ["orderActionButtonDesktop", "orderActionButtonMobile"].forEach((id) => {
    const e = el(id);
    if (e) e.innerHTML = html;
  });
}

/* Switching tokens used to reload the page, re-rendering the whole menu
   to show a different order's items. The menu never changes between
   tokens, so only the Current Order panel updates; the URL follows with
   pushState so refresh, back and bookmarks still land on a real page. */
export function createTokenSwitcher({ doc = document, win = globalThis, getOrderId, setOrderId, onSwitch }) {
  function markCurrent(orderId) {
    doc.querySelectorAll(".active-order-item.current").forEach((e) => e.classList.remove("current"));
    doc.querySelectorAll(`.active-order-item[data-order-id="${orderId}"]`).forEach((e) => e.classList.add("current"));
  }

  function switchTo(orderId) {
    setOrderId(orderId);
    markCurrent(orderId);
    onSwitch(orderId);
  }

  win.addEventListener("popstate", () => {
    const match = win.location.pathname.match(/\/token\/(\d+)\/bill\//);
    if (!match) return;
    const orderId = parseInt(match[1], 10);
    if (orderId === getOrderId()) return;
    switchTo(orderId);
  });

  return {
    /* A tap on an order in the Active list. Returns false to keep the link from navigating. */
    select(orderId, url) {
      if (orderId === getOrderId()) return "same";
      if (url) win.history.pushState({ orderId }, "", url);
      switchTo(orderId);
      return "switched";
    },
  };
}

/* One class on <html> scales the menu and cart for a screen seen from a distance. */
export function createLargeText({ doc = document, storage = globalThis.localStorage }) {
  const KEY = "rasova_large_text";
  function paint() {
    const on = doc.documentElement.classList.contains("large-text");
    const icon = doc.getElementById("largeTextIcon");
    if (icon) {
      icon.className = on ? "bi bi-type-h1" : "bi bi-type";
      icon.style.color = on ? "var(--accent-gold)" : "";
    }
  }
  return {
    init() {
      try {
        if (storage.getItem(KEY) === "1") doc.documentElement.classList.add("large-text");
      } catch (noStorage) { /* default size */ }
      paint();
    },
    toggle() {
      const on = doc.documentElement.classList.toggle("large-text");
      try {
        storage.setItem(KEY, on ? "1" : "0");
      } catch (noStorage) { /* this page only */ }
      paint();
    },
  };
}

/* Two drag handles between the three desktop panels. The menu in the
   middle is never given a width: it takes what the sides leave. */
export function setupPanelResizer({ doc = document, storage = globalThis.localStorage }) {
  const STORAGE_KEY = "rasova_billing_panel_widths";
  const DEFAULTS = { "category-sidebar": 210, "context-sidebar": 350 };
  const MIN = { "category-sidebar": 150, "context-sidebar": 280 };
  const MAX = { "category-sidebar": 320, "context-sidebar": 480 };
  const MIN_MENU_WIDTH = 320;

  function loadWidths() {
    try {
      return { ...DEFAULTS, ...JSON.parse(storage.getItem(STORAGE_KEY) || "{}") };
    } catch (bad) {
      return { ...DEFAULTS };
    }
  }
  function saveWidths(widths) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(widths));
    } catch (noStorage) { /* this visit only */ }
  }
  function applyWidths(widths) {
    const cat = doc.querySelector(".category-sidebar");
    const ctx = doc.querySelector(".context-sidebar");
    if (cat) cat.style.width = widths["category-sidebar"] + "px";
    if (ctx) ctx.style.width = widths["context-sidebar"] + "px";
  }
  function clamp(panelKey, value, workspace) {
    value = Math.max(MIN[panelKey], Math.min(MAX[panelKey], value));
    const other = panelKey === "category-sidebar" ? "context-sidebar" : "category-sidebar";
    const otherWidth = parseFloat(doc.querySelector("." + other)?.style.width) || DEFAULTS[other];
    const available = workspace.clientWidth - otherWidth - MIN_MENU_WIDTH;
    return Math.max(MIN[panelKey], Math.min(value, available));
  }
  function initHandle(handleId, panelKey, direction) {
    const handle = doc.getElementById(handleId);
    const panel = doc.querySelector("." + panelKey);
    const workspace = doc.querySelector(".workspace");
    if (!handle || !panel || !workspace) return;
    let dragging = false;
    let startX = 0;
    let startWidth = 0;
    handle.addEventListener("pointerdown", (e) => {
      dragging = true;
      startX = e.clientX;
      startWidth = panel.getBoundingClientRect().width;
      handle.classList.add("dragging");
      handle.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    handle.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      panel.style.width = clamp(panelKey, startWidth + (e.clientX - startX) * direction, workspace) + "px";
    });
    const endDrag = () => {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove("dragging");
      const widths = loadWidths();
      widths[panelKey] = parseFloat(panel.style.width) || DEFAULTS[panelKey];
      saveWidths(widths);
    };
    handle.addEventListener("pointerup", endDrag);
    handle.addEventListener("pointercancel", endDrag);
    handle.addEventListener("dblclick", () => {
      panel.style.width = DEFAULTS[panelKey] + "px";
      const widths = loadWidths();
      widths[panelKey] = DEFAULTS[panelKey];
      saveWidths(widths);
    });
  }

  applyWidths(loadWidths());
  // A rightward drag grows the left panel and shrinks the right one.
  initHandle("handleLeft", "category-sidebar", 1);
  initHandle("handleRight", "context-sidebar", -1);
}
