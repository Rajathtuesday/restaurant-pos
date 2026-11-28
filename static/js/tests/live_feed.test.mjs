/*
 * The reader every live screen shares (static/js/core/live_feed.js) and
 * the signed-out notice (static/js/core/signed_out.js): which answer is
 * drawn when answers arrive out of order, what a failure says, and what
 * signing out does. The screens' own tests check each one uses it.
 *
 * Why (28 Nov 2026): on a cook's phone with a slow link the kitchen board
 * dropped every answer because a newer read had always gone out already,
 * and a signed-out dashboard went on asking once a minute for hours.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createLiveFeed } from "../core/live_feed.js";
import { knownSignedOut, showSignedOut, SIGN_IN, signedOut } from "../core/signed_out.js";
import { networkDown, reply, scriptedFetch } from "./helpers.mjs";

/* A fetch whose answers the test hands out by hand, in any order. */
function heldFetch() {
  const waiting = [];
  const fetchFn = (url, options) => new Promise((resolve, reject) => waiting.push({ url, options, resolve, reject }));
  return { fetchFn, waiting };
}

function feedWith(fetchFn, extra = {}) {
  const seen = { data: [], failures: [], signedOut: 0 };
  const feed = createLiveFeed({
    fetchFn, address: "/live/",
    onData: (d) => seen.data.push(d.n), onFailure: (m) => seen.failures.push(m),
    onSignedOut: () => { seen.signedOut += 1; },
    ...extra,
  });
  return { feed, seen };
}

test("an answer is drawn when it was asked after what is on screen, whatever else is still out", async () => {
  const { fetchFn, waiting } = heldFetch();
  const { feed, seen } = feedWith(fetchFn);
  const one = feed.refresh();
  const two = feed.refresh();
  const three = feed.refresh();
  waiting[1].resolve(reply(200, { n: 2 }));
  assert.equal(await two, true, "drawn although question three is still out");
  waiting[0].resolve(reply(200, { n: 1 }));
  assert.equal(await one, false, "older than what is on screen: dropped");
  waiting[2].resolve(reply(200, { n: 3 }));
  assert.equal(await three, true);
  assert.deepEqual(seen.data, [2, 3]);
});

test("an older failure never wipes a newer answer, and a newer failure is shown", async () => {
  const { fetchFn, waiting } = heldFetch();
  const { feed, seen } = feedWith(fetchFn);
  const one = feed.refresh();
  const two = feed.refresh();
  const three = feed.refresh();
  waiting[1].resolve(reply(200, { n: 2 }));
  await two;
  waiting[0].reject(networkDown());
  assert.equal(await one, false);
  assert.deepEqual(seen.failures, []);
  waiting[2].reject(networkDown());
  await three;
  assert.deepEqual(seen.failures, ["Check the connection."]);
});

test("an answer older than a failure on screen is dropped too", async () => {
  const { fetchFn, waiting } = heldFetch();
  const { feed, seen } = feedWith(fetchFn);
  const one = feed.refresh();
  const two = feed.refresh();
  waiting[1].resolve(reply(502, undefined));
  await two;
  waiting[0].resolve(reply(200, { n: 1 }));
  assert.equal(await one, false);
  assert.deepEqual(seen, { data: [], failures: ["Server error (502)."], signedOut: 0 });
});

test("restart drops the answers to every question asked before it", async () => {
  const { fetchFn, waiting } = heldFetch();
  let station = "";
  const { feed, seen } = feedWith(fetchFn, { address: () => "/live/" + station });
  const before = feed.refresh();
  station = "bar";
  const after = feed.restart();
  assert.deepEqual(waiting.map((w) => w.url), ["/live/", "/live/bar"]);
  waiting[0].resolve(reply(200, { n: 1 }));
  assert.equal(await before, false, "an answer for the station just left");
  waiting[1].resolve(reply(200, { n: 2 }));
  assert.equal(await after, true);
  assert.deepEqual(seen.data, [2]);
});

test("poll waits for a read on its way, until that read is older than patience", async () => {
  let clock = 1000;
  const { fetchFn, waiting } = heldFetch();
  const { feed } = feedWith(fetchFn, { now: () => clock, patience: 15000 });
  feed.poll();
  clock += 14999;
  assert.equal(await feed.poll(), false);
  assert.equal(waiting.length, 1);
  clock += 1;
  feed.poll();
  assert.equal(waiting.length, 2, "a read out for 15 seconds may have hung: ask again");
});

test("poll asks again once the read on its way has come back", async () => {
  const { fetchFn, waiting } = heldFetch();
  const { feed } = feedWith(fetchFn);
  const first = feed.poll();
  waiting[0].resolve(reply(200, { n: 1 }));
  await first;
  feed.poll();
  assert.equal(waiting.length, 2);
});

test("what a failure says: the server's words, an error page, a page that is not data, no answer", async () => {
  const fetchFn = scriptedFetch([
    reply(400, { error: "Station not found" }),
    reply(403, { message: "Only the kitchen can see this" }),
    reply(500, {}),
    reply(502, undefined),
    reply(200, undefined),
    networkDown(),
  ]);
  const { feed, seen } = feedWith(fetchFn);
  for (let i = 0; i < 6; i += 1) assert.equal(await feed.refresh(), false);
  assert.deepEqual(seen.failures, [
    "Station not found", "Only the kitchen can see this", "Server error (500).",
    "Server error (502).", "Check the connection.", "Check the connection.",
  ]);
});

test("a screen chooses which of a refusal's words to show", async () => {
  const fetchFn = scriptedFetch([reply(500, { error: "tables_data_failed", message: "Could not load table data." })]);
  const { feed, seen } = feedWith(fetchFn, { words: (d) => d.message || d.error });
  await feed.refresh();
  assert.deepEqual(seen.failures, ["Could not load table data."]);
});

test("an answer the screen cannot use is a failure in its words; a bug in reading it is not 'the connection'", async () => {
  const fetchFn = scriptedFetch([reply(200, { n: 1 }), reply(200, { n: 2 })]);
  let calls = 0;
  const read = (d) => {
    calls += 1;
    if (calls === 1) throw new Error("Invalid data from server.");
    return d.missing.deeper;                // a TypeError in the screen's own code
  };
  const { feed, seen } = feedWith(fetchFn, { read });
  await feed.refresh();
  await feed.refresh();
  assert.equal(seen.failures[0], "Invalid data from server.");
  assert.notEqual(seen.failures[1], "Check the connection.");
});

test("signed out (401): the notice once, nothing drawn or failed, and no more asking", async () => {
  const fetchFn = scriptedFetch([reply(401, { error: "You're signed out. Sign in again to carry on.", signed_out: true })]);
  const { feed, seen } = feedWith(fetchFn);
  assert.equal(await feed.refresh(), false);
  assert.equal(await feed.refresh(), false);
  assert.equal(await feed.poll(), false);
  assert.equal(await feed.restart(), false);
  assert.equal(fetchFn.calls.length, 1);
  assert.equal(feed.stopped(), true);
  assert.deepEqual(seen, { data: [], failures: [], signedOut: 1 });
});

test("signed out while other reads were out: their late answers draw nothing", async () => {
  const { fetchFn, waiting } = heldFetch();
  const { feed, seen } = feedWith(fetchFn);
  const one = feed.refresh();
  const two = feed.refresh();
  waiting[1].resolve(reply(401, { signed_out: true }));
  await two;
  waiting[0].resolve(reply(200, { n: 1 }));
  assert.equal(await one, false);
  assert.deepEqual(seen, { data: [], failures: [], signedOut: 1 });
});

test("once another script on the page found out it is signed out, a feed stops before asking", async () => {
  let notice = false;
  const fetchFn = scriptedFetch([reply(200, { n: 1 })]);
  const { feed, seen } = feedWith(fetchFn, { pageSignedOut: () => notice });
  assert.equal(await feed.refresh(), true);
  notice = true;                                   // the badges' poll got the 401 first
  assert.equal(await feed.poll(), false);
  assert.equal(await feed.refresh(), false);
  assert.equal(fetchFn.calls.length, 1);
  assert.equal(feed.stopped(), true);
  assert.equal(seen.signedOut, 0, "the notice is already up: not shown twice");
});

test("knownSignedOut: only a notice that is actually showing", () => {
  const page = (hidden) => ({ querySelector: (s) => (s === "[data-signed-out]" ? { hidden } : null) });
  assert.equal(knownSignedOut(page(false)), true);
  assert.equal(knownSignedOut(page(true)), false);
  assert.equal(knownSignedOut({ querySelector: () => ({}) }), false, "something that is not the notice");
  assert.equal(knownSignedOut({ querySelector: () => null }), false);
  assert.equal(knownSignedOut(undefined), false);
});

test("forget drops what is on its way without asking again", async () => {
  const { fetchFn, waiting } = heldFetch();
  const { feed, seen } = feedWith(fetchFn);
  const one = feed.refresh();
  feed.forget();
  assert.equal(waiting.length, 1, "nothing new asked");
  waiting[0].resolve(reply(200, { n: 1 }));
  assert.equal(await one, false);
  assert.deepEqual(seen.data, []);
});

test("with a timeout, a hung read is given up as a connection failure", async () => {
  const hanging = (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  });
  const { feed, seen } = feedWith(hanging, { timeout: 20 });
  assert.equal(await feed.refresh(), false);
  assert.deepEqual(seen.failures, ["Check the connection."]);
});

test("without a timeout no signal is added: the screen's own options go as they are", async () => {
  const fetchFn = scriptedFetch([reply(200, { n: 1 })]);
  const { feed } = feedWith(fetchFn);
  await feed.refresh();
  assert.equal("signal" in fetchFn.calls[0].options, false);
});

test("every read asks for JSON and keeps the screen's own options", async () => {
  const fetchFn = scriptedFetch([reply(200, { n: 1 })]);
  const { feed } = feedWith(fetchFn, { init: { credentials: "same-origin", headers: { "X-Extra": "1" } } });
  await feed.refresh();
  assert.deepEqual(fetchFn.calls[0].options, { credentials: "same-origin", headers: { Accept: "application/json", "X-Extra": "1" } });
});

test("signedOut: a 401, or the sign-in page reached by a redirect; nothing else", () => {
  assert.equal(SIGN_IN, "/login/");
  assert.equal(signedOut(reply(401, {})), true);
  assert.equal(signedOut({ ...reply(200, undefined), redirected: true, url: "https://spice.rasova.net/login/?next=/kitchen/" }), true);
  assert.equal(signedOut({ ...reply(200, {}), redirected: true, url: "https://spice.rasova.net/dashboard/" }), false);
  assert.equal(signedOut({ ...reply(200, {}), redirected: false, url: "https://spice.rasova.net/login/" }), false);
  assert.equal(signedOut(reply(403, {})), false);
  assert.equal(signedOut(null), false);
});

test("showSignedOut shows the page's notice once; a page without one is left alone", () => {
  const note = { hidden: true };
  const doc = { querySelector: (s) => (s === "[data-signed-out]" ? note : null) };
  assert.equal(showSignedOut(doc), true);
  assert.equal(note.hidden, false);
  assert.equal(showSignedOut(doc), false, "already shown");
  assert.equal(showSignedOut({ querySelector: () => null }), false);
});
