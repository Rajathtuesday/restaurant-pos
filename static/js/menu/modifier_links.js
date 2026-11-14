/*
 * The ingredient links of one modifier (menu/modifiers_management.html's
 * Inventory Link sheet), as markup. Every name is escaped: the sheet used
 * to put ingredient names, which staff type, straight into the page.
 * Kept apart from the page wiring (modifiers.js) so node can test it.
 */
import { esc } from "../order_edit.js";

export function linksHtml(links) {
  if (!links || !links.length) return '<p class="small text-muted">No ingredients linked yet.</p>';
  return '<p class="small text-uppercase fw-bold mb-2 link-head">Existing Links</p>' + links.map((l) => `
    <div class="d-flex justify-content-between align-items-center py-1 border-bottom">
      <span class="small">${esc(l.inventory_item_name)} &times;${esc(l.quantity)}${esc(l.unit)}</span>
      <button type="button" class="btn-icon" data-action="modlink.remove" data-item="${esc(l.inventory_item_id)}"
              aria-label="Remove ${esc(l.inventory_item_name)}"><i class="bi bi-x-circle text-danger" aria-hidden="true"></i></button>
    </div>`).join("");
}

/* Options left visible by the item filter; the first of them when the
   chosen one was filtered out. */
export function filterOptions(options, query) {
  const q = String(query || "").trim().toLowerCase();
  let first = null;
  for (const opt of options) {
    const shown = !q || opt.textContent.toLowerCase().includes(q);
    opt.hidden = !shown;
    if (shown && !first) first = opt;
  }
  return first;
}
