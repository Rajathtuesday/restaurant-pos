/*
 * What a cart will cost, before the order exists: the browser copy of the tax
 * engine (orders/services/tax_engine.py), for the carts on the POS, the QSR
 * counter and the QR menu. The bill itself is always totalled on the server;
 * this has to show the same figures first, so it follows the engine's rules
 * for a cart (carts have no discounts):
 *   - each line at its own kind of tax and rate: GST, or VAT for liquor;
 *     a 0% line stays 0%
 *   - tax added on top when prices exclude it, inside the price when they
 *     include it, chosen separately for GST and for VAT; an outlet on the
 *     composition scheme collects no GST, nor does one without a GSTIN
 *     (VAT is not GST)
 *   - each kind's tax on the dishes is added up exactly and rounded once to
 *     the paisa, half up; the parcel charge is taxed at its own GST rate and
 *     rounded on its own
 *   - the total is rounded to the rupee, half up
 * It counts in whole paise, and rates in basis points (so 5.5% is exact),
 * which makes its rounding come out as the server's does.
 *
 * RasovaCartTax.totals(lines, outlet)
 *   lines:  [{amount: price x quantity in rupees, kind: "gst" | "vat", rate: percent}]
 *           ({amount, gstRate} is still read as a GST line)
 *   outlet: {inclusive (GST prices include GST), vatInclusive, composition,
 *            gstRegistered (false: no GSTIN, so no GST; true if left out),
 *            parcel (rupees), parcelGstRate (percent)}
 * returns, in rupees: {subtotal, gst, vat, parcel, parcelGst, total, roundedTotal, roundOff}
 *   subtotal  the sum of the line amounts, as the menu shows them
 *   gst, vat  all GST (the parcel charge's included) and all VAT on the bill
 *   total     what the guest pays before rounding to the rupee
 *
 * orders/tests/test_cart_tax_js.py runs this in Node against the real engine.
 */
(function (root) {
  "use strict";

  const toPaise = (rupees) => Math.round((Number(rupees) || 0) * 100);
  const toBasisPoints = (percent) => Math.round((Number(percent) || 0) * 100);

  // Round exact paise half up. On top of the price the exact figure is a
  // whole number of 1/10000 paise, so integer maths decides it exactly;
  // inside the price a tie can't happen at these rates, and the tiny nudge
  // only guards against floating-point noise.
  function onTopPaise(numerator) {
    return Math.floor((numerator + 5000) / 10000);
  }
  function insidePaise(exact) {
    return Math.round(exact + 1e-9);
  }

  // One kind's tax on its dishes: exact sum, rounded once.
  function kindTax(byRate, inclusive) {
    if (inclusive) {
      let exact = 0;
      for (const [rateBp, amount] of byRate) exact += (amount * rateBp) / (10000 + rateBp);
      return insidePaise(exact);
    }
    let numerator = 0;
    for (const [rateBp, amount] of byRate) numerator += amount * rateBp;
    return onTopPaise(numerator);
  }

  function totals(lines, outlet) {
    const o = outlet || {};
    const collectsGst = !o.composition && o.gstRegistered !== false;
    const inside = { gst: !!o.inclusive, vat: !!o.vatInclusive };
    const byKind = { gst: new Map(), vat: new Map() };
    const amountByKind = { gst: 0, vat: 0 };
    let subtotal = 0;
    for (const line of lines || []) {
      const amount = toPaise(line.amount);
      const kind = line.kind === "vat" ? "vat" : "gst";
      const rateBp = toBasisPoints(line.rate !== undefined ? line.rate : line.gstRate);
      subtotal += amount;
      amountByKind[kind] += amount;
      if (kind === "gst" && !collectsGst) continue;
      if (rateBp > 0) byKind[kind].set(rateBp, (byKind[kind].get(rateBp) || 0) + amount);
    }

    const gstDishes = kindTax(byKind.gst, inside.gst);
    const vat = kindTax(byKind.vat, inside.vat);

    // The parcel charge: its own GST rate, rounded on its own.
    const parcel = toPaise(o.parcel);
    const parcelRateBp = toBasisPoints(o.parcelGstRate);
    let parcelGst = 0;
    if (parcel > 0 && parcelRateBp > 0 && collectsGst) {
      parcelGst = inside.gst
        ? insidePaise((parcel * parcelRateBp) / (10000 + parcelRateBp))
        : onTopPaise(parcel * parcelRateBp);
    }

    // The guest pays every line, plus the tax of each kind priced without it.
    let total = subtotal + parcel;
    if (!inside.gst) total += gstDishes + parcelGst;
    if (!inside.vat) total += vat;
    const roundedTotal = Math.floor((total + 50) / 100);
    return {
      subtotal: subtotal / 100,
      gst: (gstDishes + parcelGst) / 100,
      vat: vat / 100,
      parcel: parcel / 100,
      parcelGst: parcelGst / 100,
      total: total / 100,
      roundedTotal: roundedTotal,
      roundOff: (roundedTotal * 100 - total) / 100,
    };
  }

  // The page's dish tax table ({dish id: {kind, rate}}), which the server
  // renders with json_script from the same rule the bill uses
  // (orders/services/tax_service.py, sale_tax_map). {} if the page has none.
  function readTable(elementId) {
    const el = typeof document !== "undefined" ? document.getElementById(elementId) : null;
    return el ? JSON.parse(el.textContent) : {};
  }

  // A cart line for a dish: its tax from the table, or GST at the rate the
  // page gave if the dish isn't in it.
  function line(amount, table, dishId, fallbackGstRate) {
    const tax = (table || {})[String(dishId)];
    return tax
      ? { amount: amount, kind: tax.kind, rate: Number(tax.rate) }
      : { amount: amount, kind: "gst", rate: Number(fallbackGstRate) || 0 };
  }

  const api = { totals: totals, readTable: readTable, line: line };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.RasovaCartTax = api;
  }
})(typeof window !== "undefined" ? window : this);
