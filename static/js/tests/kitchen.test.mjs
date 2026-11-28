/*
 * The kitchen display's modules (static/js/kitchen/), with fakes for the
 * server: what a ticket shows, what each tap sends, and what happens when
 * the server refuses, the connection is down, an answer arrives late, or
 * a button is tapped twice.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createKitchenActions } from "../kitchen/actions.js";
import { allDone, boardHtml, colorClass, failedHtml, minutesSince } from "../kitchen/board.js";
import { createKitchenLight, KEY } from "../kitchen/light.js";
import { createKitchenLoader } from "../kitchen/loader.js";
import { fakeUi, networkDown, reply, scriptedFetch } from "./helpers.mjs";

const URLS = {
  kitchen_data: "/kitchen-data/",
  item_start: "/item-start/0/",
  mark_ready: "/item-ready/0/",
  bump_kot: "/bump-kot/0/",
  clear_all: "/kitchen/clear-all/",
};

const NOW = Date.parse("2026-11-13T12:00:00Z");
const ago = (minutes) => new Date(NOW - minutes * 60000).toISOString();
const item = (id, status, extra = {}) => ({ id, name: "Dish " + id, quantity: 1, status, notes: "", ...extra });
const kot = (id, minutes, items, extra = {}) => ({
  id, kot_number: id, station: "Grill", table: "T" + id, order_id: id * 10, created_at: ago(minutes), items, ...extra,
});

/* A fake api: records what was posted, answers from a list (an Error is thrown). */
function fakeApi(answers = []) {
  const posts = [];
  return {
    posts,
    post: async (url, body) => {
      posts.push([url, body]);
      const next = answers.shift();
      if (next instanceof Error) throw next;
      return next || { success: true };
    },
  };
}

/* ---- what a ticket shows -------------------------------------------------- */

test("a ticket's age: whole minutes, never negative, 0 for a time that cannot be read", () => {
  assert.equal(minutesSince(ago(14.9), NOW), 14);
  assert.equal(minutesSince(new Date(NOW + 60000).toISOString(), NOW), 0);
  assert.equal(minutesSince("not a time", NOW), 0);
  assert.equal(minutesSince(null, NOW), 0);
});

test("gold under 15 minutes, amber under 25, red after", () => {
  assert.deepEqual([0, 14, 15, 24, 25, 90].map(colorClass),
    ["card-fresh", "card-fresh", "card-warn", "card-warn", "card-urgent", "card-urgent"]);
});

test("each dish gets the button for its next step", () => {
  const html = boardHtml([kot(1, 3, [item(11, "sent"), item(12, "preparing"), item(13, "ready")])], NOW);
  assert.match(html, /data-action="item\.start" data-id="11"/);
  assert.match(html, /data-action="item\.ready" data-id="12"/);
  assert.doesNotMatch(html, /data-id="13"/);
  assert.match(html, /badge-done/);
  assert.match(html, /card-fresh/);
  assert.match(html, />3m</);
});

test("bump is offered until every dish on the ticket is ready", () => {
  assert.equal(allDone(kot(1, 1, [item(1, "ready"), item(2, "sent")])), false);
  assert.match(boardHtml([kot(1, 1, [item(1, "ready"), item(2, "sent")])], NOW), /data-action="kot\.bump" data-id="1"/);
  assert.doesNotMatch(boardHtml([kot(1, 1, [item(1, "ready"), item(2, "ready")])], NOW), /kot\.bump/);
});

test("what people typed is escaped: dish, note, table", () => {
  const html = boardHtml([kot(1, 1, [item(1, "sent", { name: "<img src=x onerror=a()>", notes: '"><script>b()</script>' })],
    { table: "<b>T1</b>" })], NOW);
  assert.doesNotMatch(html, /<img|<script|<b>T1/);
  assert.match(html, /&lt;img src=x/);
  assert.match(html, /&lt;b&gt;T1/);
});

test("no tickets is All Caught Up; a failure is not", () => {
  assert.match(boardHtml([], NOW), /All Caught Up/);
  const failed = failedHtml("<i>down</i>");
  assert.match(failed, /Sync Failed/);
  assert.doesNotMatch(failed, /All Caught Up|<i>down/);
});

test("no markup the board draws carries a style or an onclick", () => {
  const html = boardHtml([kot(1, 30, [item(1, "sent", { notes: "no onions" })])], NOW) + boardHtml([], NOW) + failedHtml("x");
  assert.doesNotMatch(html, /style=|onclick=/);
});

/* ---- asking for the tickets ----------------------------------------------- */

function loaderWith(script) {
  const seen = { kots: [], failures: [] };
  const fetchFn = scriptedFetch(script);
  const loader = createKitchenLoader({
    fetchFn, url: URLS.kitchen_data,
    onKots: (kots) => seen.kots.push(kots),
    onFailure: (message) => seen.failures.push(message),
  });
  return { loader, seen, fetchFn };
}

test("the tickets are handed to the page", async () => {
  const { loader, seen, fetchFn } = loaderWith([reply(200, { kots: [kot(1, 1, [item(1, "sent")])] })]);
  assert.equal(await loader.refresh(), true);
  assert.equal(seen.kots[0][0].id, 1);
  assert.equal(fetchFn.calls[0].url, "/kitchen-data/");
});

test("a station is sent with its name encoded, and All Stations sends none", async () => {
  const { loader, fetchFn } = loaderWith([reply(200, { kots: [] }), reply(200, { kots: [] })]);
  await loader.setStation("Chef's Grill & Bar");
  assert.equal(fetchFn.calls[0].url, "/kitchen-data/?station=Chef's%20Grill%20%26%20Bar");
  await loader.setStation("");
  assert.equal(fetchFn.calls[1].url, "/kitchen-data/");
});

test("a refusal is a failure with the server's words, never an empty board", async () => {
  const { loader, seen } = loaderWith([reply(400, { error: "Could not load the kitchen display. Please try again." })]);
  assert.equal(await loader.refresh(), false);
  assert.deepEqual(seen.kots, []);
  assert.deepEqual(seen.failures, ["Could not load the kitchen display. Please try again."]);
});

test("a page that is not Rasova's data (a Wi-Fi portal), or an error page, says so", async () => {
  const { loader, seen } = loaderWith([reply(200, undefined), reply(502, undefined)]);
  await loader.refresh();
  await loader.refresh();
  assert.deepEqual(seen.failures, ["Check the connection.", "Server error (502)."]);
});

test("signed out, the notice is shown once and the board stops asking", async () => {
  const signedOut = [];
  const fetchFn = scriptedFetch([reply(401, { error: "You're signed out. Sign in again to carry on.", signed_out: true })]);
  const seen = { kots: [], failures: [] };
  const loader = createKitchenLoader({
    fetchFn, url: URLS.kitchen_data,
    onKots: (k) => seen.kots.push(k), onFailure: (m) => seen.failures.push(m),
    onSignedOut: () => signedOut.push(true),
  });
  assert.equal(await loader.refresh(), false);
  assert.equal(await loader.poll(), false);
  assert.equal(await loader.setStation("Bar"), false);
  assert.equal(fetchFn.calls.length, 1, "nothing more is asked once signed out");
  assert.deepEqual(signedOut, [true]);
  assert.deepEqual(seen, { kots: [], failures: [] }, "the board is not told to fail: the notice says it all");
});

test("a slow link: an answer is drawn even while a newer read is on its way", async () => {
  // 28 Nov 2026: every answer said the whisky was ready, but each was
  // dropped because the next 5-second read had already gone out, so the
  // board showed Ready for seconds and the cook tapped it twice more.
  const waiting = [];
  const fetchFn = () => new Promise((resolve) => waiting.push(resolve));
  const drawn = [];
  const loader = createKitchenLoader({ fetchFn, url: URLS.kitchen_data, onKots: (k) => drawn.push(k[0].items[0].status), onFailure: () => {} });
  const afterTap = loader.refresh();              // asked after the Ready tap
  const nextTick = loader.refresh();              // the timer asks again before the answer came
  waiting[0](reply(200, { kots: [kot(1, 1, [item(1, "ready")])] }));
  assert.equal(await afterTap, true, "the answer to the read after the tap is drawn");
  waiting[1](reply(200, { kots: [kot(1, 1, [item(1, "ready")])] }));
  assert.equal(await nextTick, true);
  assert.deepEqual(drawn, ["ready", "ready"]);
});

test("the timer does not pile reads onto a slow link, but a hung read does not stop it", async () => {
  let clock = 0;
  const waiting = [];
  const fetchFn = () => new Promise((resolve) => waiting.push(resolve));
  const loader = createKitchenLoader({ fetchFn, url: URLS.kitchen_data, onKots: () => {}, onFailure: () => {}, now: () => clock });
  const first = loader.poll();
  clock = 5000;
  assert.equal(await loader.poll(), false, "one read is still on its way: the timer waits");
  assert.equal(waiting.length, 1);
  clock = 20000;
  const second = loader.poll();                   // the first has hung past patience
  assert.equal(waiting.length, 2);
  waiting[1](reply(200, { kots: [] }));
  assert.equal(await second, true);
  waiting[0](reply(200, { kots: [] }));
  assert.equal(await first, false, "the hung read's late answer is older than the one drawn");
});

test("a tap is always asked for at once, even with a read on its way", async () => {
  const waiting = [];
  const fetchFn = () => new Promise((resolve) => waiting.push(resolve));
  const loader = createKitchenLoader({ fetchFn, url: URLS.kitchen_data, onKots: () => {}, onFailure: () => {} });
  loader.poll();
  loader.refresh();
  assert.equal(waiting.length, 2);
});

test("the connection being down says to check it", async () => {
  const { loader, seen } = loaderWith([networkDown()]);
  await loader.refresh();
  assert.deepEqual(seen.failures, ["Check the connection."]);
});

test("only the newest answer is drawn: the station just left does not come back", async () => {
  let releaseOld;
  const answers = [
    new Promise((resolve) => { releaseOld = () => resolve(reply(200, { kots: [kot(1, 1, [item(1, "sent")])] })); }),
    Promise.resolve(reply(200, { kots: [kot(2, 1, [item(2, "sent")])] })),
  ];
  const seen = [];
  const loader = createKitchenLoader({
    fetchFn: () => answers.shift(), url: URLS.kitchen_data,
    onKots: (kots) => seen.push(kots.map((k) => k.id)), onFailure: () => seen.push("failed"),
  });
  const old = loader.refresh();
  assert.equal(await loader.setStation("Bar"), true);
  releaseOld();
  assert.equal(await old, false);
  assert.deepEqual(seen, [[2]]);
});

/* ---- what a tap sends ----------------------------------------------------- */

function actionsWith(api, ui = fakeUi()) {
  let refreshed = 0;
  const actions = createKitchenActions({ api, ui, urls: URLS, refresh: async () => { refreshed += 1; } });
  return { actions, ui, refreshed: () => refreshed };
}

test("start, ready and bump post to the dish or the ticket, then ask for the board again", async () => {
  const api = fakeApi();
  const { actions, refreshed } = actionsWith(api);
  assert.equal(await actions.start("11"), true);
  assert.equal(await actions.ready("11"), true);
  assert.equal(await actions.bump("3"), true);
  assert.deepEqual(api.posts.map((p) => p[0]), ["/item-start/11/", "/item-ready/11/", "/bump-kot/3/"]);
  assert.equal(refreshed(), 3);
});

test("a second tap while the first is on its way is ignored", async () => {
  const api = fakeApi();
  const { actions } = actionsWith(api);
  const [first, second] = await Promise.all([actions.start(11), actions.start(11)]);
  assert.deepEqual([first, second], [true, false]);
  assert.equal(api.posts.length, 1);
});

test("a refusal asks for the board again: the button tapped was most likely stale", async () => {
  // 28 Nov 2026: a board a few seconds behind kept a Ready button on a
  // whisky already ready, and the cook tapped it twice more.
  const api = fakeApi([new Error("Item not found")]);
  const { actions, refreshed } = actionsWith(api);
  assert.equal(await actions.ready(11), false);
  assert.equal(refreshed(), 1);
  assert.equal(await actions.ready(11), true, "the dish can be tapped again after a refusal");
  assert.equal(refreshed(), 2);
});

test("clear all asks first, and says how many were cleared", async () => {
  const api = fakeApi([{ success: true, count: 4 }]);
  const { actions, ui, refreshed } = actionsWith(api);
  assert.equal(await actions.clearAll(), true);
  assert.deepEqual(api.posts, [["/kitchen/clear-all/", {}]]);
  assert.deepEqual(ui.calls.toast, [["Cleared 4 item(s)", "success"]]);
  assert.equal(refreshed(), 1);
});

test("clear all answered No sends nothing", async () => {
  const api = fakeApi();
  const ui = fakeUi();
  ui.confirm = async () => ({ isConfirmed: false });
  const { actions, refreshed } = actionsWith(api, ui);
  await actions.clearAll();
  assert.deepEqual(api.posts, []);
  assert.equal(refreshed(), 0);
});

/* ---- the kitchen's own light switch --------------------------------------- */

function fakeDoc() {
  const classes = new Set();
  const icon = { className: "" };
  const scheme = { content: "", setAttribute(name, value) { this[name] = value; } };
  return {
    classes, icon, scheme,
    documentElement: {
      classList: {
        add: (c) => classes.add(c),
        toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
      },
    },
    getElementById: (id) => (id === "themeIcon" ? icon : null),
    querySelector: () => scheme,
  };
}

function fakeStorage(start = {}) {
  const kept = { ...start };
  return { kept, getItem: (k) => (k in kept ? kept[k] : null), setItem: (k, v) => { kept[k] = String(v); } };
}

test("the kitchen is dark until its own switch says light, whatever the app's theme", () => {
  const doc = fakeDoc();
  const light = createKitchenLight({ doc, storage: fakeStorage({ dark: "false" }) });
  light.show();
  assert.deepEqual([...doc.classes], ["dark"]);
  assert.equal(doc.icon.className, "bi bi-sun");
  assert.equal(doc.scheme.content, "dark");
});

test("the switch flips the screen, the icon and the browser's colours, and is remembered", () => {
  const doc = fakeDoc();
  const storage = fakeStorage();
  const light = createKitchenLight({ doc, storage });
  assert.equal(light.toggle(), true);
  assert.ok(doc.classes.has("kitchen-light"));
  assert.equal(doc.icon.className, "bi bi-moon");
  assert.equal(doc.scheme.content, "light");
  assert.equal(storage.kept[KEY], "true");

  const again = createKitchenLight({ doc: fakeDoc(), storage });
  assert.equal(again.isLight(), true);
  assert.equal(again.toggle(), false);
  assert.equal(storage.kept[KEY], "false");
});

test("a private window with no storage still works for the page", () => {
  const doc = fakeDoc();
  const storage = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };
  const light = createKitchenLight({ doc, storage });
  assert.equal(light.isLight(), false);
  assert.equal(light.toggle(), true);
  assert.ok(doc.classes.has("kitchen-light"));
});

test("a dish's modifiers show on the kitchen card, each once, apart from the typed note", () => {
  // A guest's QR sauce has no note: without the modifiers the cook never saw it.
  const html = boardHtml([kot(1, 1, [item(1, "sent", { modifiers: ["Korean Chilli"], notes: "extra crispy" })])], NOW);
  assert.equal(html.split("Korean Chilli").length - 1, 1);
  assert.match(html, /\+ Korean Chilli/);
  assert.match(html, /extra crispy/);
});
