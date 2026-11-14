/*
 * The inventory board's pure parts (inventory/inventory_board.html): the
 * line that says what Generate Purchase Orders did, as markup with every
 * supplier and ingredient name escaped (they are typed by staff; the page
 * used to put them in as HTML), and the search. Kept apart from the page
 * wiring (board.js) so node can test them.
 */
import { esc } from "../order_edit.js";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function poResultHtml(data, { poListUrl, supplierUrl }) {
  if (!data.has_suppliers && data.processed_count === 0) {
    return "You haven't added any suppliers yet. Add one to start generating purchase orders. " +
      `<a class="po-link" href="${esc(supplierUrl)}">Add a Supplier</a>`;
  }
  const orders = data.purchase_orders || [];
  let html = `${plural(data.processed_count, "item")} added to ${plural(orders.length, "draft PO")}`;
  if (orders.length) {
    html += ": " + orders.map((po) => `<a class="po-link" href="${esc(poListUrl)}#po-row-${esc(po.id)}">${esc(po.supplier)}</a>`).join(", ");
  }
  const skipped = data.skipped || [];
  if (skipped.length) {
    html += `<br><span class="po-skipped">Skipped: ${skipped.map((s) => `${esc(s.name)} (${esc(s.reason)})`).join(", ")}</span>`;
  }
  return html;
}

/* Rows whose first cell holds the search, shown; the rest hidden. */
export function searchRows(rows, query) {
  const q = String(query || "").toLowerCase();
  let shown = 0;
  for (const row of rows) {
    const cell = row.querySelector("td:first-child");
    const show = !q || (cell ? cell.textContent.toLowerCase() : "").includes(q);
    row.hidden = !show;
    if (show) shown += 1;
  }
  return shown;
}
