/*
 * Setup, Offers: the example bill beside the form, worked out with the
 * menu's real prices by the same engine every cart uses (offers.js) and the
 * same tax sums (cart_tax.js). Pure: the page module draws what this returns.
 */
import { evaluate } from "../offers.js";
import { line, totals } from "../cart_tax.js";

export const EXAMPLE_NAMES = {
  buy_get_free: "Buy 2 pitchers, get 1 free", happy_hour: "Happy hour 25% off",
  percent_off: "10% off desserts", amount_off: "₹100 off every whisky",
};

/* The draft offer, as offers.js reads one. The example shows the offer while
   it runs, so it leaves out the days, times and dates. */
export function draftRule(template, form) {
  return {
    id: 1, name: (form.name || "").trim() || EXAMPLE_NAMES[template], priority: 0,
    kind: template === "buy_get_free" ? "buy_get_free" : (template === "amount_off" ? "amount_off" : "percent_off"),
    buy: Number(form.buy) || 0, free: Number(form.free) || 0,
    percent: form.percent || 0, amount: form.amount || 0,
    dishes: form.dishes || [], categories: form.categories || [], windows: [],
  };
}

export function covered(menu, rule) {
  if (!rule.dishes.length && !rule.categories.length) return menu;
  return menu.filter((d) => rule.dishes.includes(d.id) || rule.categories.includes(d.category));
}

/* A sensible first example: one full group for "buy N get M", one of each
   (up to three) otherwise; the owner can change any quantity. */
export function defaultQty(rule, dishes) {
  const qty = {};
  if (rule.kind === "buy_get_free") {
    const units = Math.max(rule.buy + rule.free, 1);
    const shown = dishes.slice(0, Math.min(3, units));
    shown.forEach((d, i) => { qty[d.id] = i === 0 ? units - (shown.length - 1) : 1; });
  } else {
    dishes.slice(0, 3).forEach((d) => { qty[d.id] = 1; });
  }
  return qty;
}

/* {quantities, lines: [{dish, qty, amount, offer: {name, discount} | null}],
   totals, groupSize}, or null when the offer covers no dish.
   qty: dish id -> quantity, or {} for the default example. */
export function exampleBill({ menu, rule, qty, dishTax, outlet }) {
  const dishes = covered(menu, rule).slice(0, 6);
  if (!dishes.length) return null;
  const quantities = Object.keys(qty || {}).length ? qty : defaultQty(rule, dishes);
  const count = (d) => quantities[d.id] || 0;
  const cart = dishes.map((d, i) => ({
    key: i, position: i, dishId: d.id, categoryId: d.category, unitPrice: d.price,
    quantity: count(d), addedAt: null, eligible: true,
  }));
  const applied = evaluate(cart, [rule], {});
  const taxLines = dishes.map((d, i) => {
    const t = line(Number(d.price) * count(d), dishTax, d.id, 0);
    t.offer = applied[i] ? applied[i].discount : 0;
    return t;
  });
  return {
    quantities,
    lines: dishes.map((d, i) => ({
      dish: d, qty: count(d), amount: Number(d.price) * count(d),
      offer: applied[i] ? { name: applied[i].offerName, discount: applied[i].discount } : null,
    })),
    totals: totals(taxLines, outlet),
    groupSize: rule.kind === "buy_get_free" ? rule.buy + rule.free : 0,
  };
}
