/*
 * The owner's dashboard "today" block (static/js/dashboard/today.js): drawing
 * the server's ready-made blocks, escaping, and the minute's refresh.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { alertsHtml, clock, createTodayRefresh, drawBlock } from "../dashboard/today.js";
import { networkDown, reply } from "./helpers.mjs";

const BLOCK = {
  outlet: "Main", revenue: "₹1,25,000", orders: 40, avg: "₹3,125", tables: 5, kitchen: 2,
  alerts: [{ level: "alert", words: "1 void today", href: "/reports/audit/" }],
};

function fakeCard() {
  const fields = {};
  return {
    fields,
    querySelector: (selector) => {
      const name = selector.match(/data-field="(\w+)"/)[1];
      fields[name] = fields[name] || { textContent: "", innerHTML: "" };
      return fields[name];
    },
  };
}

test("the alerts in words, as links, escaped; none says so", () => {
  const html = alertsHtml([{ level: "warn", words: "<b>₹450</b> given", href: "/a/?x=1&y=2" }]);
  assert.match(html, /class="today-alert is-warn"/);
  assert.match(html, /&lt;b&gt;₹450&lt;\/b&gt; given/);
  assert.match(html, /href="\/a\/\?x=1&amp;y=2"/);
  assert.match(alertsHtml([]), /Nothing needs a look/);
});

test("an alert whose report does not open here is words, never a link to a 403", () => {
  const html = alertsHtml([{ level: "warn", words: "₹450 given in discounts", href: "" }]);
  assert.match(html, /<span class="today-alert is-warn">₹450 given in discounts<\/span>/);
  assert.doesNotMatch(html, /<a /);
});

test("a block goes into its card", () => {
  const card = fakeCard();
  drawBlock(card, BLOCK);
  assert.equal(card.fields.revenue.textContent, "₹1,25,000");
  assert.equal(card.fields.orders.textContent, "40");
  assert.equal(card.fields.kitchen.textContent, "2");
  assert.match(card.fields.alerts.innerHTML, /1 void today/);
});

test("the refresh draws the server's blocks and says when", async () => {
  const drawn = [];
  const said = [];
  const refresh = createTodayRefresh({
    fetchFn: async () => reply(200, { today: [BLOCK] }), url: "/dashboard/metrics.json",
    draw: (blocks) => drawn.push(blocks), say: (w) => said.push(w), now: () => new Date(2026, 10, 13, 9, 5),
  });
  assert.equal(await refresh(), true);
  assert.equal(drawn[0][0].revenue, "₹1,25,000");
  assert.deepEqual(said, ["Updated 9:05"]);
  assert.equal(clock(new Date(2026, 0, 1, 21, 0)), "21:00");
});

test("a failed refresh keeps the numbers and says they are the last ones", async () => {
  for (const answer of [networkDown(), reply(500, {}), reply(200, undefined), reply(200, { error: "x" })]) {
    const said = [];
    let drew = false;
    const refresh = createTodayRefresh({
      fetchFn: async () => { if (answer instanceof Error) throw answer; return answer; },
      url: "/m", draw: () => { drew = true; }, say: (w) => said.push(w),
    });
    assert.equal(await refresh(), false);
    assert.equal(drew, false);
    assert.deepEqual(said, ["Could not update: these are the last numbers"]);
  }
});

test("a newer answer is drawn while an even newer refresh is still out", async () => {
  const waiting = [];
  const drawn = [];
  const refresh = createTodayRefresh({
    fetchFn: () => new Promise((resolve) => waiting.push(resolve)), url: "/m",
    draw: (b) => drawn.push(b[0].revenue), say: () => {},
  });
  const tap = refresh();          // the refresh button
  const minute = refresh();       // the minute's refresh, before the first came back
  waiting[0](reply(200, { today: [{ ...BLOCK, revenue: "first" }] }));
  assert.equal(await tap, true);
  waiting[1](reply(200, { today: [{ ...BLOCK, revenue: "second" }] }));
  assert.equal(await minute, true);
  assert.deepEqual(drawn, ["first", "second"]);
});

test("signed out, the numbers stop refreshing and the notice is shown once", async () => {
  const signedOut = [];
  const said = [];
  let asked = 0;
  const refresh = createTodayRefresh({
    fetchFn: async () => { asked += 1; return reply(401, { error: "You're signed out. Sign in again to carry on.", signed_out: true }); },
    url: "/m", draw: () => assert.fail("nothing to draw"), say: (w) => said.push(w), onSignedOut: () => signedOut.push(true),
  });
  assert.equal(await refresh(), false);
  assert.equal(await refresh(), false);
  assert.equal(asked, 1);
  assert.deepEqual(signedOut, [true]);
  assert.deepEqual(said, [], "no \"last numbers\" line under the notice");
});

test("only the newest answer is drawn", async () => {
  let release;
  const answers = [
    new Promise((r) => { release = () => r(reply(200, { today: [{ ...BLOCK, revenue: "old" }] })); }),
    Promise.resolve(reply(200, { today: [{ ...BLOCK, revenue: "new" }] })),
  ];
  const drawn = [];
  const refresh = createTodayRefresh({ fetchFn: () => answers.shift(), url: "/m", draw: (b) => drawn.push(b[0].revenue), say: () => {} });
  const first = refresh();
  await refresh();
  release();
  await first;
  assert.deepEqual(drawn, ["new"]);
});
