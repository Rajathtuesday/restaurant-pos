/*
 * The browser copy of the offer engine (offers/engine.py), so a cart can show
 * what an offer takes off before the order exists. The bill is always worked
 * out on the server; this follows the same rules to the paisa:
 *   - a unit takes an offer only if the offer was live when its line was
 *     added (dates, days and windows follow the business day: 1 AM Saturday
 *     is Friday night; a window whose end is not after its start runs past
 *     midnight; no times means the whole business day)
 *   - offers are tried by priority, then by what each is worth on its own,
 *     then oldest first; a line one offer touched is closed to the others
 *   - buy N get M free: eligible units dearest first (ties: earlier line,
 *     then its first unit), groups of N + M, the cheapest M of each full
 *     group free; percent off and amount off (never more than the price)
 *     on each unit
 *   - a line's discount is the exact sum over its units rounded once to the
 *     paisa, half up, and never more than price x quantity
 * It counts in whole paise and percents in basis points, so its rounding is
 * the server's. offers/tests/test_offers_js.py runs it in Node against the
 * Python engine.
 *
 * RasovaOffers.evaluate(lines, rules, {cutoffHour})
 *   lines: [{key, position, dishId, categoryId, unitPrice (rupees), quantity,
 *            addedAt ("YYYY-MM-DDTHH:MM:SS", the outlet's local time), eligible}]
 *   rules: [{id, name, kind, buy, free, percent, amount, priority, dishes: [ids],
 *            categories: [ids], windows: [{days: [0-6], start: "HH:MM", end: "HH:MM"}],
 *            validFrom: "YYYY-MM-DD", validUntil, liveUntil: "YYYY-MM-DDTHH:MM:SS"}]
 * returns {key: {offerId, offerName, discount (rupees), freeUnits}}
 *
 * RasovaOffers.outletNow(utcOffsetMinutes) is the outlet's local time now, in
 * that same "YYYY-MM-DDTHH:MM:SS" form, whatever the device's own time zone.
 */
(function (root) {
  "use strict";

  const toPaise = (rupees) => Math.round((Number(rupees) || 0) * 100);
  const toBasisPoints = (percent) => Math.round((Number(percent) || 0) * 100);
  const pad = (n) => String(n).padStart(2, "0");

  // "YYYY-MM-DDTHH:MM:SS" (local wall time) -> parts, without any time zone.
  function parts(moment) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/.exec(moment || "");
    if (!m) return null;
    return { y: +m[1], mo: +m[2], d: +m[3], h: +m[4], mi: +m[5], s: +(m[6] || 0) };
  }

  function minutesOf(hhmm) {
    if (!hhmm) return null;
    const [h, m, s] = String(hhmm).split(":").map(Number);
    return h * 60 + (m || 0) + (s || 0) / 60;
  }

  // The business date ("YYYY-MM-DD"), its weekday (Monday 0) and the clock
  // minutes of a local moment.
  function businessMoment(p, cutoffHour) {
    const shifted = new Date(Date.UTC(p.y, p.mo - 1, p.d, p.h, p.mi, p.s) - cutoffHour * 3600 * 1000);
    const date = `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`;
    return { date: date, weekday: (shifted.getUTCDay() + 6) % 7, clock: p.h * 60 + p.mi + p.s / 60 };
  }

  function inWindow(w, bm, cutoffHour) {
    const days = w.days || [];
    if (days.length && !days.includes(bm.weekday)) return false;
    let start = minutesOf(w.start), end = minutesOf(w.end);
    if (start === null && end === null) return true;
    if (start === null) start = cutoffHour * 60;
    if (end === null) end = cutoffHour * 60;
    const sinceOpen = (minutes) => (((minutes - cutoffHour * 60) % 1440) + 1440) % 1440;
    const s = sinceOpen(start);
    let e = sinceOpen(end);
    const now = sinceOpen(bm.clock);
    if (e <= s) e = 1440;
    return s <= now && now < e;
  }

  function liveAt(rule, moment, cutoffHour) {
    const windows = rule.windows || [];
    if (!moment) return !windows.length && !rule.validFrom && !rule.validUntil;
    if (rule.liveUntil && moment >= rule.liveUntil) return false;
    const p = parts(moment);
    if (!p) return false;
    const bm = businessMoment(p, cutoffHour);
    if (rule.validFrom && bm.date < rule.validFrom) return false;
    if (rule.validUntil && bm.date > rule.validUntil) return false;
    if (windows.length && !windows.some((w) => inWindow(w, bm, cutoffHour))) return false;
    return true;
  }

  function covers(rule, line) {
    const dishes = rule.dishes || [], categories = rule.categories || [];
    if (!dishes.length && !categories.length) return true;
    return dishes.includes(line.dishId) || (line.categoryId != null && categories.includes(line.categoryId));
  }

  // Discounts are kept exact in units of 1/10000 paise ("ticks"), so a
  // percent off is a whole number and rounding happens once per line.
  const TICKS = 10000;

  function run(rule, units) {
    const taken = [];
    if (rule.kind === "buy_get_free") {
      const buy = Number(rule.buy) || 0, free = Number(rule.free) || 0, size = buy + free;
      if (buy < 1 || free < 1) return [];
      const ordered = units.slice().sort((a, b) =>
        b.price - a.price || a.line.position - b.line.position || a.index - b.index);
      for (let start = 0; start + size <= ordered.length; start += size) {
        const group = ordered.slice(start, start + size);
        group.forEach((u, i) => { u.ticks = i < buy ? 0 : u.price * TICKS; taken.push(u); });
      }
    } else if (rule.kind === "percent_off") {
      const bp = Math.min(toBasisPoints(rule.percent), 10000);
      if (bp <= 0) return [];
      units.forEach((u) => { u.ticks = u.price * bp; taken.push(u); });
    } else if (rule.kind === "amount_off") {
      const amount = toPaise(rule.amount);
      if (amount <= 0) return [];
      units.forEach((u) => { u.ticks = Math.min(amount, u.price) * TICKS; taken.push(u); });
    }
    return taken;
  }

  function evaluate(lines, rules, opts) {
    const cutoffHour = (opts && opts.cutoffHour != null) ? opts.cutoffHour : 6;
    const ordered = (lines || []).slice().sort((a, b) => a.position - b.position);
    const units = [];
    for (const line of ordered) {
      const price = toPaise(line.unitPrice), quantity = Math.max(parseInt(line.quantity, 10) || 0, 0);
      if (line.eligible === false || quantity <= 0 || price <= 0) continue;
      for (let index = 0; index < quantity; index++) units.push({ line: line, index: index, price: price, ticks: 0 });
    }
    const candidates = (rule, closed) => units.filter((u) =>
      !closed.has(u.line.key) && covers(rule, u.line) && liveAt(rule, u.line.addedAt, cutoffHour));
    const worth = (rule) => run(rule, candidates(rule, new Set()).map((u) => ({ ...u, ticks: 0 })))
      .reduce((sum, u) => sum + u.ticks, 0);

    const known = (rules || []).filter((r) => ["buy_get_free", "percent_off", "amount_off"].includes(r.kind));
    const ranked = known.map((r) => ({ rule: r, worth: worth(r) }))
      .sort((a, b) => (b.rule.priority || 0) - (a.rule.priority || 0) || b.worth - a.worth || a.rule.id - b.rule.id)
      .map((x) => x.rule);

    const closed = new Set(), result = {};
    for (const rule of ranked) {
      const open = candidates(rule, closed);
      open.forEach((u) => { u.ticks = 0; });
      const byLine = new Map();
      for (const u of run(rule, open)) {
        if (!byLine.has(u.line.key)) byLine.set(u.line.key, []);
        byLine.get(u.line.key).push(u);
      }
      for (const [key, lineUnits] of byLine) {
        const line = lineUnits[0].line;
        const ticks = lineUnits.reduce((sum, u) => sum + u.ticks, 0);
        let paise = Math.floor((ticks + TICKS / 2) / TICKS);
        paise = Math.min(paise, toPaise(line.unitPrice) * line.quantity);
        closed.add(key);
        if (paise > 0) {
          const freeUnits = rule.kind === "buy_get_free"
            ? lineUnits.filter((u) => u.ticks === u.price * TICKS).length : 0;
          result[key] = { offerId: rule.id, offerName: rule.name, discount: paise / 100, freeUnits: freeUnits };
        }
      }
    }
    return result;
  }

  function outletNow(utcOffsetMinutes) {
    const d = new Date(Date.now() + (Number(utcOffsetMinutes) || 0) * 60000);
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` +
      `T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  }

  // The page's offers ({rules, dishes: {id: {category, price}}, cutoffHour,
  // utcOffset}), rendered by the server with json_script; null without one.
  function readPage(elementId) {
    const el = typeof document !== "undefined" ? document.getElementById(elementId) : null;
    return el ? JSON.parse(el.textContent) : null;
  }

  // Offers for a cart: cart lines are [{dishId, quantity, eligible}], all
  // added now. Returns the evaluate() result keyed by the cart line's index.
  function forCart(page, cartLines) {
    if (!page || !page.rules || !page.rules.length) return {};
    const now = outletNow(page.utcOffset);
    const lines = cartLines.map((c, i) => {
      const dish = (page.dishes || {})[String(c.dishId)] || {};
      return {
        key: i, position: i, dishId: Number(c.dishId), categoryId: dish.category == null ? null : dish.category,
        unitPrice: dish.price || 0, quantity: c.quantity, addedAt: now, eligible: c.eligible !== false,
      };
    });
    return evaluate(lines, page.rules, { cutoffHour: page.cutoffHour });
  }

  // For a cart page: puts each line's offer on its tax line (the
  // RasovaCartTax.line() objects, so totals() takes it off before tax) and
  // returns what forCart() found, by cart index. items: [{dishId, quantity,
  // takeaway}]. opts.none: no offers at all (an order through Zomato or
  // Swiggy); opts.parcel: the whole order is a parcel. Liquor in a parcel
  // never takes an offer, as on the bill (offers/services.py).
  function onCart(page, taxLines, items, opts) {
    const o = opts || {};
    taxLines.forEach((l) => { l.offer = 0; });
    if (!page || o.none) return {};
    const applied = forCart(page, items.map((it, i) => ({
      dishId: it.dishId, quantity: it.quantity,
      eligible: !(taxLines[i] && taxLines[i].kind === "vat" && (it.takeaway || o.parcel)),
    })));
    taxLines.forEach((l, i) => { if (applied[i]) l.offer = applied[i].discount; });
    return applied;
  }

  // "Buy 2 get 1  -Rs 2300.00" as safe HTML for a cart row, or "".
  function note(applied) {
    if (!applied) return "";
    const name = String(applied.offerName).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
    return `<div class="cart-offer" style="font-size:0.72rem;color:#10b981;">${name} &minus;&#8377;${applied.discount.toFixed(2)}</div>`;
  }

  const api = {
    evaluate: evaluate, liveAt: liveAt, outletNow: outletNow, readPage: readPage, forCart: forCart,
    onCart: onCart, note: note,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.RasovaOffers = api;
  }
})(typeof window !== "undefined" ? window : this);
