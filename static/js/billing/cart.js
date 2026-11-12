/*
 * The cart on a billing screen, before the order exists: what is in it and
 * what it will cost. Pure: no DOM, no network. The screen renders it; the
 * server totals the real bill.
 *
 *   const cart = createCart({ onChange });   // called after every change, to redraw
 *   cart.add({ id, name, price, gst_percentage, is_veg, note, is_takeaway, modifiers });
 *   cart.inc(index); cart.dec(index); cart.clear();
 *   cart.totals({ dishTax, pageOffers, outlet, source, operatorSources, parcel })
 *     -> { lines, applied, totals }
 *
 * A line is one dish with one note and one takeaway flag: adding the same
 * dish with the same note again raises its quantity. Money goes through the
 * browser copies of the tax and offer engines (cart_tax.js, offers.js),
 * which the parity tests hold equal to the server's.
 */
import { line as taxLine, totals as taxTotals } from "../cart_tax.js";
import { onCart as offersOnCart } from "../offers.js";

export function createCart({ onChange = () => {} } = {}) {
  let items = [];

  function sameLine(a, b) {
    return a.id === b.id && (a.note || "") === (b.note || "") && !!a.is_takeaway === !!b.is_takeaway;
  }

  return {
    get items() {
      return items;
    },

    get count() {
      return items.reduce((sum, i) => sum + i.quantity, 0);
    },

    isEmpty() {
      return items.length === 0;
    },

    /* One more of a dish. `mergeBy` "line" (the default) matches dish, note
       and takeaway flag; "dish" matches the dish alone, the counter's one-tap
       add, where a note never exists. */
    add(dish, { mergeBy = "line" } = {}) {
      const entry = {
        id: dish.id,
        name: dish.name,
        price: Number(dish.price),
        quantity: 1,
        note: dish.note || "",
        is_takeaway: !!dish.is_takeaway,
        modifiers: dish.modifiers || [],
        gst_percentage: Number(dish.gst_percentage) || 0,
        is_veg: dish.is_veg !== false,
        parcel_charge: Number(dish.parcel_charge) || 0,   // the dish's own, if it has one
      };
      const existing = mergeBy === "dish"
        ? items.find((i) => i.id === entry.id)
        : items.find((i) => sameLine(i, entry));
      if (existing) existing.quantity += 1;
      else items.push(entry);
      onChange();
      return existing || entry;
    },

    inc(index) {
      if (!items[index]) return;
      items[index].quantity += 1;
      onChange();
    },

    dec(index) {
      if (!items[index]) return;
      items[index].quantity -= 1;
      if (items[index].quantity <= 0) items.splice(index, 1);
      onChange();
    },

    clear() {
      items = [];
      onChange();
    },

    /* How many of a dish, across its lines (the badge on a menu card). */
    quantityOf(id) {
      return items.filter((i) => i.id === id).reduce((sum, i) => sum + i.quantity, 0);
    },

    /* The cart as /create-order/ reads it. */
    toPayload() {
      // The dish's parcel charge is the screen's preview only: the server
      // reads its own.
      return items.map(({ parcel_charge: preview, ...i }) => ({ ...i, modifiers: [...i.modifiers] }));
    },

    /*
     * What the cart costs, the bill's way.
     *   dishTax         {dishId: {kind, rate}} from the page (tax_service.sale_tax_map)
     *   pageOffers      the page's offers block, or null
     *   outlet          {inclusive, vatInclusive, composition, gstRegistered, parcelGstRate}
     *   source          "dine_in", "takeaway", "counter", "zomato", ...
     *   operatorSources the sources whose app pays the GST (Zomato, Swiggy, ...)
     *   parcel          the parcel charge in rupees when it is on, else 0
     */
    totals({ dishTax = {}, pageOffers = null, outlet = {}, source = "dine_in", operatorSources = [], parcel = 0 } = {}) {
      const operator = operatorSources.includes(source);
      const lines = items.map((i) => taxLine(i.price * i.quantity, dishTax, i.id, i.gst_percentage));
      const applied = offersOnCart(
        pageOffers,
        lines,
        items.map((i) => ({ dishId: i.id, quantity: i.quantity, takeaway: !!i.is_takeaway })),
        { none: operator, parcel: source === "takeaway" },
      );
      const totals = taxTotals(lines, {
        inclusive: !!outlet.inclusive,
        vatInclusive: !!outlet.vatInclusive,
        composition: !!outlet.composition,
        gstRegistered: outlet.gstRegistered !== false,
        gstPaidByOperator: operator,
        parcel: parcel || 0,
        parcelGstRate: outlet.parcelGstRate || 0,
      });
      return { lines, applied, totals };
    },
  };
}
