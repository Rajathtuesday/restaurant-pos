/*
 * The menu side of a billing screen: the category list, the search box,
 * the item sheet with its modifiers, and the counter's one-tap add.
 */
import { rupees } from "../core/money.js";
import { esc } from "../order_edit.js";

export function createMenu({ doc = document, modifiersUrl, fetchFn = (...a) => fetch(...a), win = globalThis }) {
  let selected = null;
  let modal = null;
  let opening = false;          // the sheet is still sliding in
  let closeWhenOpen = false;

  const el = (id) => doc.getElementById(id);

  /* ---- categories ------------------------------------------------------- */

  function activateCategory(item) {
    doc.querySelectorAll(".category-item").forEach((i) => i.classList.remove("active"));
    if (!item) return;
    // The desktop list and the phone strip hold the same categories in the
    // same order: light up the same position in both.
    const idx = Array.from(item.parentNode.children).indexOf(item);
    [".category-sidebar .category-item", ".mobile-category-nav .category-item"].forEach((sel) => {
      const items = doc.querySelectorAll(sel);
      if (items[idx]) items[idx].classList.add("active");
    });
  }

  function scrollToCategory(targetId, item) {
    activateCategory(item);
    const target = el(targetId);
    if (!target) return;
    if (win.innerWidth < 992) {
      const navH = doc.querySelector(".mobile-category-nav")?.offsetHeight || 60;
      const headerH = doc.querySelector(".pos-header")?.offsetHeight || 60;
      const top = target.getBoundingClientRect().top + win.scrollY - headerH - navH - 12;
      win.scrollTo({ top, behavior: "smooth" });
    } else {
      const area = doc.querySelector(".menu-area");
      const top = target.getBoundingClientRect().top - area.getBoundingClientRect().top + area.scrollTop - 24;
      area.scrollTo({ top, behavior: "smooth" });
    }
  }

  function activateFirstCategory() {
    const first = doc.querySelector(".category-sidebar .category-item") || doc.querySelector(".mobile-category-nav .category-item");
    if (first) activateCategory(first);
  }

  /* ---- search ---------------------------------------------------------- */

  function filter(text) {
    const t = (text || "").toLowerCase().trim();
    doc.querySelectorAll("h3.menu-section-title").forEach((header) => {
      const row = header.nextElementSibling;
      if (!row) return;
      let anyVisible = false;
      row.querySelectorAll(".menu-item-searchable").forEach((card) => {
        const name = card.querySelector(".item-name");
        const matches = !t || (name && name.textContent.toLowerCase().includes(t));
        card.style.display = matches ? "" : "none";
        if (matches) anyVisible = true;
      });
      header.style.display = anyVisible || !t ? "" : "none";
      row.style.display = anyVisible || !t ? "" : "none";
    });
  }

  /* ---- the item sheet -------------------------------------------------- */

  function dishFrom(card) {
    const d = card.dataset;
    return {
      id: Number(d.id),
      name: d.name,
      price: Number(d.price),
      gst_percentage: Number(d.gst) || 0,
      is_veg: d.veg !== "0",
      parcel_charge: Number(d.parcel) || 0,
    };
  }

  function modifierHtml(groups) {
    if (!groups || !groups.length) return "<p class='text-center text-muted py-2 small'>No modifiers available.</p>";
    return groups.map((g) => {
      const type = g.max_select === 1 ? "radio" : "checkbox";
      const inputName = "mod_" + String(g.group_name).replace(/\s+/g, "");
      const hint = g.max_select === 1 ? "Pick 1" : `Pick up to ${esc(g.max_select)}`;
      const inputs = g.modifiers.map((m) => `
        <div class="form-check">
          <input class="form-check-input modifier-check" type="${type}" name="${esc(inputName)}" value="${esc(m.name)}"
                 data-price="${esc(m.price)}" data-group="${esc(g.group_name)}" data-max="${esc(g.max_select)}" id="mod_${esc(m.id)}" data-action="modifier.limit">
          <label class="form-check-label d-flex justify-content-between w-100" for="mod_${esc(m.id)}">
            <span>${esc(m.name)}</span>${Number(m.price) > 0 ? `<span class="text-muted">+${esc(rupees(m.price))}</span>` : ""}
          </label>
        </div>`).join("");
      return `<div class="mb-3" data-group="${esc(g.group_name)}" data-max="${esc(g.max_select)}">
        <label class="form-label fw-bold text-uppercase modifier-group-label">
          ${esc(g.group_name)} ${g.is_required ? '<span class="text-danger">*</span>' : ""}<span class="modifier-hint">${hint}</span>
        </label>${inputs}</div>`;
    }).join("");
  }

  /* A checkbox group with a limit: ticking one past the limit unticks the oldest. */
  function enforceLimit(input) {
    if (input.type !== "checkbox") return;
    const max = parseInt(input.dataset.max, 10);
    const checked = doc.querySelectorAll(`.modifier-check[data-group="${CSS.escape(input.dataset.group)}"]:checked`);
    if (checked.length > max) checked[0].checked = false;
  }

  function openSheet(dish) {
    selected = dish;
    const modalEl = el("itemModal");
    const nameEl = el("modalItemName");
    const container = el("modifierContainer");
    if (!modalEl || !nameEl || !container) return;
    nameEl.textContent = dish.name;
    const note = el("itemNote");
    const takeaway = el("itemTakeaway");
    if (note) note.value = "";
    if (takeaway) takeaway.checked = false;
    if (modifiersUrl) {
      container.innerHTML = "<p class='text-center text-muted py-3'>Checking for modifiers...</p>";
      fetchFn(modifiersUrl.replace("/0/", "/" + dish.id + "/"))
        .then((r) => r.json())
        .then((data) => {
          if (selected === dish) container.innerHTML = modifierHtml(data.groups);
        })
        .catch(() => {
          if (selected === dish) container.innerHTML = "";
        });
    } else {
      // The restaurant has no modifiers feature: nothing to ask the server.
      container.innerHTML = "";
    }
    modal = win.bootstrap.Modal.getOrCreateInstance(modalEl);
    if (!modalEl.classList.contains("show")) {
      opening = true;
      closeWhenOpen = false;
      modalEl.addEventListener("shown.bs.modal", () => {
        opening = false;
        if (closeWhenOpen) modal.hide();
        closeWhenOpen = false;
      }, { once: true });
    }
    modal.show();
  }

  /* What the sheet says: the dish with its note, modifiers and price. */
  function readSheet() {
    if (!selected) return null;
    const names = [];
    const ids = [];
    let extra = 0;
    doc.querySelectorAll(".modifier-check:checked").forEach((cb) => {
      names.push(cb.value);
      ids.push(parseInt(cb.id.replace("mod_", ""), 10));
      extra += parseFloat(cb.getAttribute("data-price") || 0);
    });
    const typed = (el("itemNote")?.value || "").trim();
    const note = [names.join(", "), typed].filter(Boolean).join(" | ");
    return {
      ...selected,
      price: selected.price + extra,
      note,
      modifiers: ids,
      is_takeaway: !!el("itemTakeaway")?.checked,
    };
  }

  function closeSheet() {
    if (!modal) return;
    // Bootstrap ignores hide() while the sheet is still sliding in, so a
    // quick "Add to Order" would leave it open: close it once it is in.
    if (opening) closeWhenOpen = true;
    else modal.hide();
  }

  /* ---- the counter's cards ------------------------------------------- */

  // A dish can be on the screen twice (under Popular and under its own
  // category): both cards carry the badge.
  const cardsOf = (id) => doc.querySelectorAll('.menu-card[data-id="' + id + '"]');

  function flashCard(id) {
    cardsOf(id).forEach((card) => {
      card.classList.add("just-added");
      setTimeout(() => card.classList.remove("just-added"), 300);
    });
  }

  function syncBadges(cart) {
    doc.querySelectorAll(".menu-card").forEach((c) => c.classList.remove("in-cart"));
    doc.querySelectorAll(".cart-badge").forEach((b) => {
      b.textContent = "0";
    });
    cart.items.forEach((item) => {
      cardsOf(item.id).forEach((card) => {
        card.classList.add("in-cart");
        const badge = card.querySelector(".cart-badge");
        if (badge) badge.textContent = cart.quantityOf(item.id);
      });
    });
  }

  return {
    dishFrom, scrollToCategory, activateFirstCategory, filter,
    openSheet, readSheet, closeSheet, enforceLimit, flashCard, syncBadges,
  };
}
