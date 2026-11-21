/*
 * The header that fits one row (static/js/core/header_fit.js, 21 Nov 2026):
 * what each control shows for a given width and the person's style.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { CORE_RANK, plan } from "../core/header_fit.js";

// The floor page's controls, with widths close to the real ones (px).
const word = (name) => 26 + name.length * 9;
function control(id, rank, { round = false } = {}) {
  return { id, rank, round, width: { both: 44 + word(id) - 20, icon: 44, word: word(id) } };
}
const FLOOR = [
  control("clock", 12, { round: true }), control("printer", 7), control("tables", 1), control("combine", 3),
  control("transfer", 4), control("live", 8), control("billing", 2), control("kitchen", 10), control("reports", 11),
  control("calls", 5), control("alerts", 6), control("history", 9, { round: true }),
  control("theme", 13, { round: true }), control("logout", 14, { round: true }),
];
const RESERVED = 52;   // the More button and its gap
const used = (items, look) => items.filter((c) => look[c.id] !== "more").reduce((s, c) => s + c.width[look[c.id]] + 8, RESERVED);
const named = (look) => Object.keys(look).filter((id) => look[id] === "both" || look[id] === "word");

test("every style fits the width it is given, wide or narrow", () => {
  for (const style of ["auto", "both", "icons", "words"]) {
    for (const width of [300, 390, 600, 768, 1024, 1366, 1920]) {
      const look = plan(FLOOR, width, style, { reserved: RESERVED });
      assert.ok(used(FLOOR, look) <= width || Object.values(look).every((v) => v === "more"),
        `${style} at ${width}: ${used(FLOOR, look)} px`);
    }
  }
});

test("auto: wide enough, everything has its name; round icons stay icons", () => {
  const look = plan(FLOOR, 4000, "auto", { reserved: RESERVED });
  assert.ok(FLOOR.filter((c) => !c.round).every((c) => look[c.id] === "both"));
  assert.ok(FLOOR.filter((c) => c.round).every((c) => look[c.id] === "icon"));
});

test("auto on a tablet: the core buttons keep their names before less used ones even show", () => {
  const look = plan(FLOOR, 1000, "auto", { reserved: RESERVED });
  const core = FLOOR.filter((c) => c.rank <= CORE_RANK).map((c) => c.id);
  assert.deepEqual(named(look).filter((id) => core.includes(id)).sort(), [...core].sort());
  assert.ok(FLOOR.filter((c) => c.rank > CORE_RANK).every((c) => look[c.id] !== "both"));
});

test("auto: a less important name never shows while a more important one does not", () => {
  for (const width of [500, 700, 900, 1100, 1300, 1500, 1700]) {
    const look = plan(FLOOR, width, "auto", { reserved: RESERVED });
    const ranksNamed = FLOOR.filter((c) => look[c.id] === "both").map((c) => c.rank);
    const ranksNot = FLOOR.filter((c) => !c.round && look[c.id] === "icon").map((c) => c.rank);
    if (ranksNamed.length && ranksNot.length) {
      assert.ok(Math.max(...ranksNamed) < Math.min(...ranksNot), `at ${width}: named ${ranksNamed} vs icons ${ranksNot}`);
    }
  }
});

test("auto on a phone: icons, the least used under More, the most important still on the row", () => {
  const look = plan(FLOOR, 360, "auto", { reserved: RESERVED });
  assert.equal(named(look).length, 0);
  assert.equal(look.tables, "icon");
  assert.equal(look.logout, "more");
  assert.equal(look.clock, "more");
});

test("icons and words never fold a name: what does not fit goes under More", () => {
  const look = plan(FLOOR, 1024, "both", { reserved: RESERVED });
  assert.ok(Object.values(look).every((v) => v === "both" || v === "more"));
  assert.ok(Object.values(look).includes("more"));
  assert.equal(look.tables, "both", "the most important stays");
});

test("icons only and words only keep their look everywhere", () => {
  assert.ok(Object.values(plan(FLOOR, 1920, "icons", { reserved: RESERVED })).every((v) => v === "icon"));
  assert.ok(Object.values(plan(FLOOR, 1920, "words", { reserved: RESERVED })).every((v) => v === "word" || v === "more"));
});
