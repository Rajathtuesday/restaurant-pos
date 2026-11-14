/*
 * The recipe import review (inventory/recipe_import_review.html): what a
 * line sends when it changes, whether every line kept for the recipe is
 * complete, and the sheet that adds a new ingredient (a name and its unit,
 * in Rasova's own sheet: the page used two prompt()s, which never show in
 * the Android app). Kept apart from the page wiring so node can test it.
 */
export const UNITS = ["pcs", "g", "kg", "ml", "l"];

export function linePayload(row) {
  const match = row.querySelector(".match-select").value;
  return {
    resolved_inventory_item_id: match && match !== "__new__" ? match : null,
    final_quantity: row.querySelector(".qty-input").value || null,
    final_unit: row.querySelector(".unit-select").value || null,
    include_in_recipe: row.querySelector(".include-checkbox").checked,
  };
}

/* True when every line kept for the recipe has an ingredient, a quantity
   and a unit: the server needs all three. */
export function readyToSave(payloads) {
  return payloads.every((p) => !p.include_in_recipe || (p.resolved_inventory_item_id && p.final_quantity && p.final_unit));
}

/* Asks for a new ingredient's name and unit. Resolves to {name, unit} or null. */
export async function askNewIngredient(swal, isDark = () => false) {
  const options = UNITS.map((u) => `<option value="${u}"${u === "g" ? " selected" : ""}>${u}</option>`).join("");
  const result = await swal.fire({
    titleText: "New ingredient",
    html:
      '<label class="ni-label" for="ni-name">Name</label>' +
      '<input id="ni-name" class="swal2-input ni-input" maxlength="100" autocomplete="off">' +
      '<label class="ni-label" for="ni-unit">Unit</label>' +
      `<select id="ni-unit" class="swal2-select ni-input">${options}</select>`,
    showCancelButton: true,
    confirmButtonText: "Add",
    confirmButtonColor: "var(--accent-gold)",
    background: isDark() ? "var(--panel-bg)" : "#ffffff",
    color: "var(--text-main)",
    focusConfirm: false,
    preConfirm: () => {
      const popup = swal.getPopup();
      const name = popup.querySelector("#ni-name").value.trim();
      const unit = popup.querySelector("#ni-unit").value;
      if (!name) {
        swal.showValidationMessage("Type the ingredient's name.");
        return false;
      }
      return { name, unit };
    },
  });
  return result.isConfirmed ? result.value : null;
}
