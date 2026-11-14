/*
 * The promo form on Setup, Promos (setup/templates/setup/setup_promos.html):
 * what it sends, and whether it may be sent. A new promo also says where
 * it runs (every outlet, or one); an edited one keeps where it was made.
 * Kept apart from the page wiring (promos.js) so node can test it.
 */
export const FIELDS = {
  name: "f-name", code: "f-code", discount_type: "f-type", discount_value: "f-value",
  min_order_value: "f-min", max_uses: "f-maxuses", valid_from: "f-from", valid_until: "f-until", description: "f-desc",
};

export function promoPayload(read, { editing, allOutlets, outletId }) {
  const payload = {
    name: read("name").trim(),
    code: read("code").trim(),
    discount_type: read("discount_type"),
    discount_value: read("discount_value"),
    min_order_value: read("min_order_value") || 0,
    max_uses: read("max_uses") || null,
    valid_from: read("valid_from") || null,
    valid_until: read("valid_until") || null,
    description: read("description").trim(),
  };
  if (!editing) {
    payload.all_outlets = Boolean(allOutlets);
    payload.outlet_id = allOutlets || !outletId ? null : outletId;
  }
  return payload;
}

/* Why the promo may not be sent yet, or "". */
export function promoProblem(payload) {
  if (!payload.name || !(Number.parseFloat(payload.discount_value) > 0)) return "Name and a valid positive value are required.";
  return "";
}
