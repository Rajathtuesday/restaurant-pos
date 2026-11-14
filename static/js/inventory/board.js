/*
 * The inventory board (inventory/inventory_board.html). Adding stock, a
 * new ingredient, an edit, wastage and a stock count are shared post
 * forms (core/post_actions.js): a refusal is a toast, where the page used
 * alert(). This module opens the Edit, Waste and Adjust sheets filled for
 * the ingredient tapped (each row's button carries its values, and the
 * sheet's form gets that ingredient's address), Generate Purchase Orders,
 * and the search.
 */
import { api } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { withId } from "../billing/urls.js";
import { poResultHtml, searchRows } from "./board_parts.js";

const el = (id) => document.getElementById(id);
const sheet = (id) => window.bootstrap.Modal.getOrCreateInstance(el(id));

function aim(formId, template, id) {
  const form = el(formId);
  form.dataset.url = withId(template, id);
  return form;
}

delegate(document.body, {
  "inv.edit": (b) => {
    const d = b.dataset;
    aim("editForm", d.url, d.id);
    el("editName").value = d.name;
    el("editCategory").value = d.category;
    el("editCostPrice").value = d.cost;
    el("editThreshold").value = d.threshold;
    el("editReorderQty").value = d.reorder;
    el("editSupplier").value = d.supplier || "";
    el("editBottleMl").value = d.bottle || "";
    sheet("editModal").show();
  },
  "inv.waste": (b) => {
    const d = b.dataset;
    aim("wastageForm", d.url, d.id);
    el("wastageItemName").textContent = d.name;
    el("wastageUnit").textContent = d.unit;
    el("wastageQty").value = "";
    el("wastageNotes").value = "";
    el("wastageReason").value = "Spillage";
    sheet("wastageModal").show();
  },
  "inv.adjust": (b) => {
    const d = b.dataset;
    aim("adjustForm", d.url, d.id);
    el("adjustItemName").textContent = d.name;
    el("adjustUnit").textContent = d.unit;
    el("adjustNewCount").value = "";
    el("adjustReason").value = "";
    el("adjustCurrentStock").textContent = `System currently shows ${d.stock} ${d.unit}`;
    sheet("adjustModal").show();
  },
  "inv.generate": async (button) => {
    button.disabled = true;
    let data;
    try {
      data = await api.post(button.dataset.url, {});
    } catch (shown) {
      button.disabled = false;
      return;
    }
    el("lowStockBarText").innerHTML = poResultHtml(data, { poListUrl: button.dataset.poList, supplierUrl: button.dataset.suppliers });
    button.remove();
  },
});

delegate(document.body, {
  "inv.search": (input) => searchRows(Array.from(document.querySelectorAll(".inventory-card tbody tr")), input.value),
}, "input");
