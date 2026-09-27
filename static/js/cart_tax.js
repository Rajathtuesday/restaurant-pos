/*
 * What a cart will cost, before the order exists: the browser copy of the tax
 * engine (orders/services/tax_engine.py), for the carts on the POS, the QSR
 * counter and the QR menu. The bill itself is always totalled on the server;
 * this has to show the same figures first, so it follows the engine's rules
 * for a cart (carts have no discounts):
 *   - each line at its own rate; a 0% line stays 0%
 *   - tax added on top when prices exclude it, inside the price when they
 *     include it; an outlet on the composition scheme collects no GST
 *   - the dishes' tax is added up exactly and rounded once to the paisa,
 *     half up; each charge (the parcel charge) is taxed at its own rate and
 *     rounded on its own
 *   - the total is rounded to the rupee, half up
 * It counts in whole paise, and in basis points for rates, so the rounding
 * comes out exactly as the server's.
 *
 * RasovaCartTax.totals(lines, outlet)
 *   lines:  [{amount: price x quantity in rupees, gstRate: percent}]
 *   outlet: {inclusive, composition, parcel (rupees), parcelGstRate (percent)}
 * returns, in rupees: {subtotal, gst, parcel, parcelGst, total, roundedTotal, roundOff}
 *   subtotal  the sum of the line amounts, as the menu shows them
 *   gst       all GST on the bill, the parcel charge's included
 *   total     what the guest pays before rounding to the rupee
 *
 * orders/tests/test_cart_tax_js.py runs this in Node against the real engine.
 */
(function (root) {
  "use strict";

  const toPaise = (rupees) => Math.round((Number(rupees) || 0) * 100);
  const toBasisPoints = (percent) => Math.round((Number(percent) || 0) * 100);

  // Exact tax on amounts (paise) at one rate (basis points): on top of them,
  // or inside them. Returned unrounded, in paise.
  function exactTax(amountPaise, rateBp, inclusive) {
    if (rateBp <= 0) return 0;
    return inclusive ? (amountPaise * rateBp) / (10000 + rateBp) : (amountPaise * rateBp) / 10000;
  }

  // Round paise half up. On top of the price the exact figure is a whole
  // number of 1/10000 paise, so integer maths decides it exactly; inside the
  // price a tie can't happen at 5% or 18%, and the tiny nudge only guards
  // against floating-point noise.
  function roundPaise(exactPaise, onTopNumerator) {
    if (onTopNumerator !== undefined) return Math.floor((onTopNumerator + 5000) / 10000);
    return Math.round(exactPaise + 1e-9);
  }

  function totals(lines, outlet) {
    const o = outlet || {};
    const inclusive = !!o.inclusive;
    let subtotal = 0;
    const byRate = new Map();
    for (const line of lines || []) {
      const amount = toPaise(line.amount);
      const rateBp = toBasisPoints(line.gstRate);
      subtotal += amount;
      if (!o.composition && rateBp > 0) byRate.set(rateBp, (byRate.get(rateBp) || 0) + amount);
    }

    // The dishes: exact tax added up, rounded once.
    let gst;
    if (inclusive) {
      let exact = 0;
      for (const [rateBp, amount] of byRate) exact += exactTax(amount, rateBp, true);
      gst = roundPaise(exact);
    } else {
      let numerator = 0;
      for (const [rateBp, amount] of byRate) numerator += amount * rateBp;
      gst = roundPaise(null, numerator);
    }

    // The parcel charge: its own rate, rounded on its own.
    const parcel = toPaise(o.parcel);
    const parcelRateBp = toBasisPoints(o.parcelGstRate);
    let parcelGst = 0;
    if (parcel > 0 && parcelRateBp > 0 && !o.composition) {
      parcelGst = inclusive
        ? roundPaise(exactTax(parcel, parcelRateBp, true))
        : roundPaise(null, parcel * parcelRateBp);
    }

    const total = inclusive ? subtotal + parcel : subtotal + gst + parcel + parcelGst;
    const roundedTotal = Math.floor((total + 50) / 100);
    return {
      subtotal: subtotal / 100,
      gst: (gst + parcelGst) / 100,
      parcel: parcel / 100,
      parcelGst: parcelGst / 100,
      total: total / 100,
      roundedTotal: roundedTotal,
      roundOff: (roundedTotal * 100 - total) / 100,
    };
  }

  const api = { totals: totals };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.RasovaCartTax = api;
  }
})(typeof window !== "undefined" ? window : this);
