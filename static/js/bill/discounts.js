/*
 * What the bill page checks before it asks the server for a discount.
 * The server checks again (orders/services/order_service.py): these only
 * save a round trip and say what is missing. Each returns the message to
 * show, or null when the form is fine.
 */
const REASON_NEEDED = "Give a reason for the discount (for example: regular guest, food complaint).";

/* A discount on the whole bill. A promo needs no reason; one typed by hand
   does (it goes on the discount audit report). */
export function billDiscountProblem({ value, reason, promoId }) {
  if (!value || value <= 0) return { message: "Enter a valid discount value", field: "value" };
  if (!promoId && String(reason || "").trim().length < 3) return { message: REASON_NEEDED, field: "reason" };
  return null;
}

/* A dish's own discount, as typed: 0 to 100, with a reason unless it is 0
   (which takes the discount off). Returns {percent, reason} or {message}. */
export function readItemDiscount(rawPercent, rawReason) {
  const raw = String(rawPercent).trim();
  const percent = Number(raw);
  const reason = String(rawReason || "").trim();
  if (raw === "" || !Number.isFinite(percent) || percent < 0 || percent > 100) {
    return { message: "Enter a discount from 0 to 100." };
  }
  if (percent > 0 && reason.length < 3) return { message: REASON_NEEDED };
  return { percent, reason };
}

/* A promo in the picker: "Happy Monday: 10% Off". */
export function promoLabel(promo) {
  return promo.discount_type === "percentage"
    ? `${promo.name}: ${promo.discount_value}% Off`
    : `${promo.name}: ₹${promo.discount_value} Flat`;
}

/* A promo with a minimum cannot go on a smaller bill. */
export function promoProblem(promo, subtotal) {
  if (promo.min_order && Number(subtotal) < Number(promo.min_order)) {
    return `Minimum order ₹${promo.min_order} required`;
  }
  return null;
}
