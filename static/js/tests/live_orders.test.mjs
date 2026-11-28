/*
 * The live orders board's modules (static/js/live_orders/): what a ticket
 * shows, the filters and search, and the loader (refused, offline, late).
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { ageText, boardHtml, counts, matches, needsAttention, syncText, ticketHtml } from "../live_orders/board.js";
import { createOrdersLoader } from "../live_orders/loader.js";
import { networkDown, reply, scriptedFetch } from "./helpers.mjs";

const item = (id, over = {}) => ({ id, name: "Dish " + id, quantity: 1, status: "sent", modifiers: [], notes: "", is_complimentary: false, can_reduce: true, needs_manager: false, ...over });
const order = (id, over = {}) => ({
  id, label: "Table " + id, kind: "table", source_display: "Dine-in", staff: "Asha", status: "open", age_minutes: 5, total: 525,
  counts: { review: 0, ready: 0 }, items: [item(id * 10)], bill_url: `/bill/${id}/`, add_url: "", detail_url: `/order/${id}/`, ...over,
});

test("attention: a dish to approve, a dish ready, or an order open 30 minutes", () => {
  assert.equal(needsAttention(order(1)), false);
  assert.equal(needsAttention(order(1, { counts: { review: 1, ready: 0 } })), true);
  assert.equal(needsAttention(order(1, { counts: { review: 0, ready: 2 } })), true);
  assert.equal(needsAttention(order(1, { age_minutes: 30 })), true);
});

test("filters and search: by kind, by attention, by any word on the ticket", () => {
  const list = [order(1), order(2, { kind: "token" }), order(3, { kind: "online", age_minutes: 45 })];
  assert.deepEqual(counts(list), { all: 3, table: 1, counter: 1, online: 1, attention: 1 });
  assert.equal(matches(list[1], "counter", ""), true);
  assert.equal(matches(list[0], "online", ""), false);
  assert.equal(matches(list[2], "attention", ""), true);
  assert.equal(matches(list[0], "all", "dish 10"), true);
  assert.equal(matches(list[0], "all", "asha"), true);
  assert.equal(matches(list[0], "all", "zzz"), false);
});

test("a ticket: escaped, its buttons, a lock for a served dish below manager", () => {
  const html = ticketHtml(order(1, {
    label: "<b>T1</b>", staff: "<i>x</i>",
    items: [item(10, { name: "<img src=x>" }), item(11, { can_reduce: false, needs_manager: true })],
  }));
  assert.ok(!html.includes("<b>T1"));
  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes("<i>x"));
  assert.match(html, /data-action="order.reduce" data-o="1" data-i="10"/);
  assert.match(html, /data-action="order.remove" data-o="1" data-i="10"/);
  assert.match(html, /bi-lock/);
  assert.match(html, /₹525/);
});

test("the board says when nothing is open, and when nothing matches", () => {
  assert.match(boardHtml([], "all", ""), /No open orders right now/);
  assert.match(boardHtml([order(1)], "online", ""), /Nothing matches this filter/);
});

test("age and freshness read as people say them", () => {
  assert.deepEqual([5, 75, 1500].map(ageText), ["5 min", "1 h 15 m", "1 d 1 h"]);
  assert.equal(syncText(2), "Live");
  assert.equal(syncText(12), "Updated 12s ago");
});

test("loader: a refusal and the connection down are failures, never an empty board", async () => {
  const seen = { orders: [], failures: [] };
  const loader = createOrdersLoader({
    fetchFn: scriptedFetch([reply(403, { error: "Permission denied" }), networkDown(), reply(200, { orders: [order(1)], summary: {} })]),
    url: "/live-orders/data/", onOrders: (d) => seen.orders.push(d), onFailure: (m) => seen.failures.push(m),
  });
  await loader.refresh();
  await loader.refresh();
  await loader.refresh();
  assert.deepEqual(seen.failures, ["Permission denied", "Check the connection."]);
  assert.equal(seen.orders.length, 1);
});

test("loader: a newer answer is drawn while an even newer read is still out", async () => {
  const waiting = [];
  const seen = [];
  const loader = createOrdersLoader({
    fetchFn: () => new Promise((resolve) => waiting.push(resolve)), url: "/x/",
    onOrders: (d) => seen.push(d.orders[0].id), onFailure: () => {},
  });
  const a = loader.refresh();
  const b = loader.refresh();
  waiting[0](reply(200, { orders: [order(1)] }));
  assert.equal(await a, true);
  waiting[1](reply(200, { orders: [order(2)] }));
  assert.equal(await b, true);
  assert.deepEqual(seen, [1, 2]);
});

test("loader: signed out, the notice is shown once and the board stops asking", async () => {
  const signedOut = [];
  const fetchFn = scriptedFetch([reply(401, { error: "You're signed out. Sign in again to carry on.", signed_out: true })]);
  const loader = createOrdersLoader({
    fetchFn, url: "/x/", onOrders: () => assert.fail("nothing to draw"), onFailure: () => assert.fail("the notice says it"),
    onSignedOut: () => signedOut.push(true),
  });
  await loader.refresh();
  await loader.poll();
  assert.equal(fetchFn.calls.length, 1);
  assert.deepEqual(signedOut, [true]);
  assert.equal(fetchFn.calls[0].options.credentials, "same-origin");
});

test("loader: a late answer to an older question is dropped", async () => {
  let releaseFirst;
  const answers = [new Promise((r) => { releaseFirst = r; }), Promise.resolve(reply(200, { orders: [order(2)] }))];
  const seen = [];
  const loader = createOrdersLoader({ fetchFn: () => answers.shift(), url: "/x/", onOrders: (d) => seen.push(d.orders[0].id), onFailure: () => {} });
  const a = loader.refresh();
  await loader.refresh();
  releaseFirst(reply(200, { orders: [order(1)] }));
  assert.equal(await a, false);
  assert.deepEqual(seen, [2]);
});
