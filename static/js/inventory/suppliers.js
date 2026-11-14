/*
 * Suppliers (inventory/suppliers.html): the search boxes (one in the
 * header, one on a phone) filter the cards with the inventory reports'
 * row filter. Adding and removing a supplier are a shared post form and
 * post button (core/post_actions.js).
 */
import { delegate } from "../core/dom.js";
import { filterRows } from "./row_filter.js";

export function supplierCountText(visible) {
  return `${visible} supplier${visible === 1 ? "" : "s"}`;
}

if (typeof document !== "undefined") {
  delegate(document.body, {
    "suppliers.search": (input) => {
      const visible = filterRows(Array.from(document.querySelectorAll(".supplier-item")), { term: input.value });
      document.getElementById("supplierCount").textContent = supplierCountText(visible);
    },
  }, "input");
}
