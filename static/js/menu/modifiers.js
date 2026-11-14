/*
 * Modifier groups (menu/modifiers_management.html). Creating a group,
 * adding or deleting a modifier, linking and unlinking a dish are shared
 * post forms and buttons (core/post_actions.js): a refusal is shown, where
 * the page used to reload as if it had worked. This module keeps the
 * Inventory Link sheet (which modifier, its links, adding and removing
 * one) and the two search boxes.
 */
import { api, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { withId } from "../billing/urls.js";
import { filterOptions, linksHtml } from "./modifier_links.js";

const sheet = document.getElementById("inventoryModal");
let modifierId = null;

async function showLinks() {
  const box = document.getElementById("existingLinks");
  try {
    const data = await api.get(withId(sheet.dataset.linksUrl, modifierId));
    box.innerHTML = linksHtml(data.links);
  } catch (shown) {
    box.innerHTML = "";
  }
}

delegate(document.body, {
  "modlink.open": (button) => {
    modifierId = button.dataset.modifier;
    document.getElementById("invModalName").textContent = button.dataset.name;
    document.getElementById("invQty").value = "";
    showLinks();
    window.bootstrap.Modal.getOrCreateInstance(sheet).show();
  },
  "modlink.remove": async (button) => {
    try {
      await api.post(withId(sheet.dataset.removeUrl, modifierId), { inventory_item_id: button.dataset.item });
      window.location.reload();
    } catch (shown) {
      /* the client showed why */
    }
  },
});

delegate(document.body, {
  "modlink.save": async (form, event) => {
    event.preventDefault();
    const body = {
      inventory_item_id: document.getElementById("invItemSelect").value,
      quantity: document.getElementById("invQty").value,
      unit: document.getElementById("invUnit").value,
    };
    if (!body.inventory_item_id || !body.quantity) {
      ui.toast("Select an ingredient and enter a quantity.", "warning");
      return;
    }
    try {
      await api.post(withId(sheet.dataset.addUrl, modifierId), body);
      window.location.reload();
    } catch (shown) {
      /* the client showed why */
    }
  },
}, "submit");

delegate(document.body, {
  "groups.search": (input) => {
    const q = input.value.trim().toLowerCase();
    for (const col of document.querySelectorAll('#modifierCardsGrid > [class*="col-"]')) {
      col.classList.toggle("filtered-out", Boolean(q) && !col.textContent.toLowerCase().includes(q));
    }
  },
  "items.filter": (input) => {
    const select = document.getElementById("linkItem");
    const first = filterOptions(Array.from(select.options), input.value);
    if (first && select.selectedOptions[0]?.hidden) select.value = first.value;
  },
}, "input");
