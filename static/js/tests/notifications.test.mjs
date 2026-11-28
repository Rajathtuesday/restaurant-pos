/*
 * The 8-second notification poll (static/js/core/notifications.js): the
 * badges, and what happens when the browser is signed out.
 *
 * Why (28 Nov 2026): a signed-out tab followed the redirect to the sign-in
 * page, could not read it, and went on asking once a minute for hours with
 * its badges frozen and nothing said.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createNotificationPoller } from "../core/notifications.js";
import { fakeUi, reply, scriptedFetch } from "./helpers.mjs";

function fakeDoc() {
  const els = {};
  const el = (id) => {
    if (!els[id]) {
      const classes = new Set(["hidden"]);
      els[id] = { textContent: "", classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), has: (c) => classes.has(c) } };
    }
    return els[id];
  };
  return { els, getElementById: el };
}

const ANSWER = {
  waiter_calls: { count: 2, items: [] },
  kitchen_messages: { count: 1, items: [] },
  qr_orders: { count: 0, items: [] },
};

test("an answer puts the counts on the badges", async () => {
  const doc = fakeDoc();
  const poller = createNotificationPoller({ fetchFn: scriptedFetch([reply(200, ANSWER)]), ui: fakeUi(), doc, nav: {} });
  await poller.poll();
  assert.equal(doc.els["notif-badge-waiter"].textContent, "3");
  assert.equal(doc.els["notif-badge-waiter"].classList.has("hidden"), false);
});

test("signed out: the notice once, the timer stopped, and nothing more is asked", async () => {
  const realSet = globalThis.setInterval;
  const realClear = globalThis.clearInterval;
  const cleared = [];
  globalThis.setInterval = () => 42;
  globalThis.clearInterval = (id) => cleared.push(id);
  try {
    const fetchFn = scriptedFetch([reply(401, { error: "You're signed out. Sign in again to carry on.", signed_out: true })]);
    let notices = 0;
    const poller = createNotificationPoller({
      fetchFn, ui: fakeUi(), doc: fakeDoc(), nav: {}, onSignedOut: () => { notices += 1; },
    });
    poller.start();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await poller.poll();
    await poller.poll();
    assert.equal(fetchFn.calls.length, 1);
    assert.equal(notices, 1);
    assert.deepEqual(cleared, [42]);
  } finally {
    globalThis.setInterval = realSet;
    globalThis.clearInterval = realClear;
  }
});

test("the sign-in page reached by a redirect is signed out too", async () => {
  const signIn = { ...reply(200, undefined), redirected: true, url: "https://spice.rasova.net/login/?next=/dashboard/" };
  const fetchFn = scriptedFetch([signIn]);
  let notices = 0;
  const poller = createNotificationPoller({ fetchFn, ui: fakeUi(), doc: fakeDoc(), nav: {}, onSignedOut: () => { notices += 1; } });
  await poller.poll();
  await poller.poll();
  assert.equal(fetchFn.calls.length, 1);
  assert.equal(notices, 1);
});

test("once another script on the page found out it is signed out, the badges stop before asking", async () => {
  const fetchFn = scriptedFetch([reply(200, ANSWER)]);
  let notice = false;
  const poller = createNotificationPoller({ fetchFn, ui: fakeUi(), doc: fakeDoc(), nav: {}, pageSignedOut: () => notice });
  await poller.poll();
  notice = true;                                   // the kitchen board got the 401 first
  await poller.poll();
  await poller.poll();
  assert.equal(fetchFn.calls.length, 1);
});

test("an ordinary failure is skipped and the next poll tries again", async () => {
  const fetchFn = scriptedFetch([reply(500, {}), reply(200, ANSWER)]);
  const doc = fakeDoc();
  const poller = createNotificationPoller({ fetchFn, ui: fakeUi(), doc, nav: {} });
  await poller.poll();
  await poller.poll();
  assert.equal(fetchFn.calls.length, 2);
  assert.equal(doc.els["notif-badge-waiter"].textContent, "3");
});
