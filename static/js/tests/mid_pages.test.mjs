/*
 * The mid-size pages moved to modules on 14 Nov 2026: the schedule
 * builder, suppliers, the pickup display board, outlet settings, GST
 * rates and the token dashboard's refresh.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { supplierCountText } from "../inventory/suppliers.js";
import { clearRetired, setCollapsed } from "../menu/gst_rows.js";
import { fieldProblem } from "../setup/outlet_settings.js";
import { dayLabel, openAssign, showCustomTimes } from "../shifts/schedule_builder.js";
import { connect, render, tilesHtml } from "../tokens/display_board.js";
import { createRefresher } from "../tokens/refresher.js";
import { reply } from "./helpers.mjs";

/* A document of plain objects, found by id. */
function docOf(elements) {
  return { getElementById: (id) => elements[id], querySelector: () => null };
}

/* ---- the schedule builder ---------------------------------------------------- */

test("schedule: a cell opens the panel for that person and day, the name as text", () => {
  const els = {
    panelPrompt: { hidden: false }, assignForm: { hidden: true },
    f_staff_id: { value: "" }, f_date: { value: "" }, f_staff_name: { textContent: "" }, f_date_display: { textContent: "" },
  };
  openAssign(docOf(els), { staff: "7", name: "Asha O'Brien <b>", date: "2026-11-16" });
  assert.equal(els.panelPrompt.hidden, true);
  assert.equal(els.assignForm.hidden, false);
  assert.equal(els.f_staff_id.value, "7");
  assert.equal(els.f_date.value, "2026-11-16");
  assert.equal(els.f_staff_name.textContent, "Asha O'Brien <b>");
  assert.match(els.f_date_display.textContent, /16/);
});

test("schedule: custom times hide when a template is picked; a bad date stays as it is", () => {
  const els = { customTimes: { hidden: false } };
  showCustomTimes(docOf(els), "3");
  assert.equal(els.customTimes.hidden, true);
  showCustomTimes(docOf(els), "");
  assert.equal(els.customTimes.hidden, false);
  assert.equal(dayLabel("not-a-date"), "not-a-date");
});

/* ---- suppliers ------------------------------------------------------------------ */

test("suppliers: the count reads right for one and for many", () => {
  assert.deepEqual([0, 1, 4].map(supplierCountText), ["0 suppliers", "1 supplier", "4 suppliers"]);
});

/* ---- the pickup display board ---------------------------------------------------- */

test("display board: each ready token a tile, escaped; empty shows the waiting line", () => {
  assert.match(tilesHtml([{ display: "A-12" }]), /<div class="num">A-12<\/div>/);
  assert.ok(!tilesHtml([{ display: "<img src=x>" }]).includes("<img"));
  const grid = { innerHTML: "x" };
  const empty = { hidden: true };
  render(grid, empty, { tokens: [] });
  assert.equal(grid.innerHTML, "");
  assert.equal(empty.hidden, false);
  render(grid, empty, { tokens: [{ display: "7" }] });
  assert.equal(empty.hidden, true);
});

test("display board: a stream when the browser has one, a poll when not, a poll after an error", async () => {
  const grid = { innerHTML: "", dataset: { stream: "/display/abc/stream/", data: "/display/abc/data/" } };
  const empty = { hidden: false };
  const opened = [];
  class FakeSource { constructor(url) { opened.push(url); FakeSource.last = this; } }
  const fetched = [];
  const fetchFn = async (url) => { fetched.push(url); return reply(200, { tokens: [{ display: "9" }] }); };
  assert.equal(connect({ grid, empty, EventSourceCls: FakeSource, fetchFn }), "stream");
  assert.deepEqual(opened, ["/display/abc/stream/"]);
  FakeSource.last.onmessage({ data: '{"tokens": [{"display": "5"}]}' });
  assert.match(grid.innerHTML, />5</);
  FakeSource.last.onmessage({ data: "not json" });
  assert.match(grid.innerHTML, />5</);
  FakeSource.last.onerror();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(fetched, ["/display/abc/data/"]);

  const timers = [];
  assert.equal(connect({ grid, empty, EventSourceCls: null, fetchFn, every: (fn, ms) => timers.push(ms) }), "poll");
  assert.deepEqual(timers, [4000]);
});

/* ---- outlet settings --------------------------------------------------------------- */

test("outlet settings: a GSTIN must be 15 characters, an FSSAI number 14 digits; empty is fine", () => {
  assert.equal(fieldProblem("gstin", ""), "");
  assert.equal(fieldProblem("gstin", "27AABCU9603R1ZX"), "");
  assert.match(fieldProblem("gstin", "27AABCU"), /15/);
  assert.equal(fieldProblem("fssai", "12345678901234"), "");
  assert.match(fieldProblem("fssai", "1234567890123A"), /14 digits/);
  assert.match(fieldProblem("fssai", "123"), /14 digits/);
});

/* ---- GST rates ----------------------------------------------------------------------- */

test("GST: a dish off a retired rate loses its tag; the banner goes with the last one", () => {
  const removed = [];
  const els = {
    "retired-4": { remove: () => removed.push("retired-4") },
    "retired-banner": { remove: () => removed.push("banner") },
  };
  let left = true;
  const doc = {
    getElementById: (id) => els[id],
    querySelector: (sel) => (sel.startsWith("#gst-4") ? { remove: () => removed.push("option-4") } : left ? {} : null),
  };
  clearRetired(doc, ["4"]);
  assert.deepEqual(removed, ["retired-4", "option-4"]);
  left = false;
  clearRetired(doc, []);
  assert.deepEqual(removed.at(-1), "banner");
});

test("GST: a category folds away and says so", () => {
  const list = { hidden: false };
  const classes = new Set();
  const card = { classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) } };
  const attrs = {};
  const doc = {
    getElementById: (id) => (id === "cat-3" ? list : card),
    querySelector: () => ({ setAttribute: (k, v) => { attrs[k] = v; } }),
  };
  setCollapsed(doc, "3", true);
  assert.equal(list.hidden, true);
  assert.ok(classes.has("collapsed"));
  assert.equal(attrs["aria-expanded"], "false");
});

/* ---- the token dashboard's refresh ------------------------------------------------------ */

test("token refresh: 15 s after the last touch; it waits while a sheet is open", () => {
  const timers = [];
  let busy = true;
  let reloads = 0;
  let resets = 0;
  const r = createRefresher({
    reload: () => { reloads += 1; },
    busy: () => busy,
    schedule: (fn, ms) => { timers.push(ms); return timers.length; },
    cancel: () => {},
    onReset: () => { resets += 1; },
  });
  r.reset();
  assert.deepEqual(timers, [15000]);
  assert.equal(resets, 1);
  assert.equal(r.fire(), false);
  assert.equal(reloads, 0);
  assert.equal(timers.length, 2);
  busy = false;
  assert.equal(r.fire(), true);
  assert.equal(reloads, 1);
});
