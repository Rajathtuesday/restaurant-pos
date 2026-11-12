/*
 * What the billing screen looks like: the cart rows, the totals, the
 * parcel button, the tabs, the phone's cart page, the send button. It
 * reads state and writes DOM; it decides nothing.
 */
import { note as offerNote } from "../offers.js";
import { esc } from "../order_edit.js";

const money = (n) => "₹" + n;
const money2 = (n) => "₹" + Number(n).toFixed(2);

export function createScreen({ doc = document, win = globalThis, mode }) {
  const el = (id) => doc.getElementById(id);
  const all = (sel) => Array.from(doc.querySelectorAll(sel));
  const token = mode === "token";

  function cartRow(item, index, applied) {
    const total = item.price * item.quantity;
    const veg = token ? "" : `<span class="veg-dot ${item.is_veg !== false ? "veg" : "nonveg"}"></span>`;
    const takeaway = item.is_takeaway
      ? (token ? ' <span class="tkw-badge">TKW</span>' : '<span class="luxury-badge badge-platform ms-1 takeaway-badge">Takeaway</span>')
      : "";
    return `<div class="cart-row">
      <div class="cart-row-main">
        <div class="cart-item-name">${veg}${esc(item.name)}${takeaway}</div>
        ${item.note ? `<div class="cart-item-note">${esc(item.note)}</div>` : ""}
        ${offerNote(applied)}
      </div>
      <div class="cart-controls">
        <div class="qty-controls">
          <button type="button" class="qty-btn" data-action="cart.dec" data-index="${index}" aria-label="One less"><i class="bi bi-dash"></i></button>
          <span class="qty-value">${item.quantity}</span>
          <button type="button" class="qty-btn" data-action="cart.inc" data-index="${index}" aria-label="One more"><i class="bi bi-plus"></i></button>
        </div>
        <div class="cart-item-price">${money(total)}</div>
      </div>
    </div>`;
  }

  return {
    renderCart(cart, applied) {
      const html = cart.isEmpty()
        ? '<div class="panel-note panel-note--empty">Empty Order</div>'
        : cart.items.map((item, i) => cartRow(item, i, applied[i])).join("");
      ["cartItemsContainerDesktop", "cartItemsContainerMobile"].forEach((id) => {
        const c = el(id);
        if (c) c.innerHTML = html;
      });
      const badge = el("fabBadge");
      if (badge) {
        badge.textContent = cart.count;
        badge.style.display = cart.count ? "flex" : "none";
      }
    },

    renderTotals(t, { gstInclusive }) {
      all(".view-subtotal").forEach((e) => { e.textContent = money(t.subtotal); });
      all(".view-offers").forEach((e) => {
        e.textContent = "−" + money2(t.offers);
        e.closest(".summary-row").classList.toggle("is-hidden", !(t.offers > 0));
      });
      all(".view-gst").forEach((e) => {
        e.textContent = money2(t.gst);
        const label = e.closest(".summary-row")?.querySelector("span:first-child");
        if (label) label.textContent = gstInclusive ? "GST (incl.)" : "GST";
      });
      all(".view-vat").forEach((e) => {
        e.textContent = money2(t.vat);
        e.closest(".summary-row").classList.toggle("is-hidden", !(t.vat > 0));
      });
      all(".view-total").forEach((e) => { e.textContent = money(t.roundedTotal); });
    },

    /* `charge` is what the cart is being charged for it right now. */
    renderParcel(parcel, charge) {
      all(".parcel-row").forEach((row) => {
        row.classList.toggle("is-hidden", !parcel.on);
        const amount = row.querySelector("span:last-child");
        if (amount) amount.textContent = money(Math.round(charge * 100) / 100);
      });
      all(".parcel-btn").forEach((btn) => btn.classList.toggle("is-on", parcel.on));
      // The offer ("Add Parcel ₹10/item") is the template's own wording.
      all(".parcel-btn-text").forEach((e) => {
        if (!e.dataset.offer) e.dataset.offer = e.textContent;
        e.textContent = parcel.on ? "Parcel Added ✓ (tap to remove)" : e.dataset.offer;
      });
    },

    /* "cart" or the other tab, on the desktop panel and the phone's page
       alike. The other tab is the running order on the dine-in screen and
       today's active orders on the token counter; a page has one of them. */
    switchTab(tab) {
      const cart = tab === "cart";
      const toggle = (id, cls, on) => { const e = el(id); if (e) e.classList.toggle(cls, on); };
      toggle("tabCart", "active", cart); toggle("tabCartMobile", "active", cart);
      toggle("cartView", "d-none", !cart); toggle("cartViewMobile", "d-none", !cart);
      toggle("cartSummaryBlock", "d-none", !cart); toggle("cartSummaryBlockMobile", "d-none", !cart);
      ["Running", "Active"].forEach((other) => {
        toggle("tab" + other, "active", !cart); toggle("tab" + other + "Mobile", "active", !cart);
      });
      ["runningView", "activeOrdersView"].forEach((view) => {
        toggle(view, "d-none", cart); toggle(view + "Mobile", "d-none", cart);
      });
    },

    openMobileCart() {
      const page = el("mobileCartPage");
      if (!page) return;
      page.classList.add("show");
      doc.body.style.overflow = "hidden";
    },

    closeMobileCart() {
      const page = el("mobileCartPage");
      if (page) {
        page.classList.remove("show");
        doc.body.style.overflow = "";
      }
      const off = el("mobileCartOffcanvas");
      if (off && win.bootstrap) win.bootstrap.Offcanvas.getInstance(off)?.hide();
    },

    mobileCartOpen() {
      const page = el("mobileCartPage");
      return !!(page && page.classList.contains("show"));
    },

    toggleSettings(view) {
      const panel = el(view === "desktop" ? "orderSettingsPanelDesktop" : "orderSettingsPanelMobile");
      if (panel) panel.classList.toggle("d-none");
    },

    /* "Table 4 · Dine-in" above the cart. */
    updateOrderSummary(view) {
      const tableSel = el(view === "desktop" ? "tableSelectDesktop" : "tableSelectMobile");
      const srcSel = el(view === "desktop" ? "sourceSelectDesktop" : "sourceSelectMobile");
      const label = el(view === "desktop" ? "orderSummaryLabel" : "orderSummaryLabelMobile");
      if (!tableSel || !srcSel || !label) return;
      const tableName = tableSel.selectedIndex >= 0 ? tableSel.options[tableSel.selectedIndex].text : "Walk-in";
      const srcName = srcSel.selectedIndex >= 0 ? srcSel.options[srcSel.selectedIndex].text : "Dine-in";
      label.textContent = tableName + " · " + srcName;
    },

    /* The send buttons while a request runs, and back. */
    sending(on, { busyLabel, idleHtml }) {
      all(".send-btn").forEach((b) => {
        b.disabled = on;
        b.innerHTML = on ? busyLabel : idleHtml;
      });
    },

    /* The small corner message the dine-in screen has always used. */
    flash(message) {
      const t = el("toastBox");
      if (!t) return;
      t.textContent = message;
      t.classList.add("show");
      setTimeout(() => t.classList.remove("show"), 3000);
    },

    showPrinterError(message) {
      const banner = el("printer-error-banner");
      const text = el("printer-error-text");
      if (!banner) return;
      if (text) text.textContent = message;
      banner.style.display = "flex";
    },

    hidePrinterError() {
      const banner = el("printer-error-banner");
      if (banner) banner.style.display = "none";
    },
  };
}
