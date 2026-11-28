/*
 * The service worker (static/sw.js), run in a sandbox: its fetch handler is
 * called with fake requests, a fake network and fake caches, and what it
 * answers is checked.
 *
 * Why (21 Nov 2026): on the live site every page logged "Uncaught (in
 * promise) TypeError: Failed to convert value to 'Response'" from sw.js. A
 * file from another host (Cloudflare's analytics beacon, which Brave blocks)
 * that was not kept and could not be fetched was answered with undefined.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../../sw.js", import.meta.url), "utf8");
const ORIGIN = "https://spice.rasova.net";

// `kept`: what the page cache holds (pages, and what the old worker kept
// beside them); `keptData`: the data cache (answers marked keep).
function worker({ network = "down", kept = {}, keptData = {}, headers = {}, cacheNames = [] } = {}) {
  const handlers = {};
  const self = {
    location: new URL(ORIGIN),
    addEventListener: (type, fn) => { handlers[type] = fn; },
    skipWaiting: async () => {},
    clients: { claim: async () => {} },
  };
  const fetched = [];
  const stored = [];
  const deleted = [];
  const keyOf = (request) => (typeof request === "string" ? new URL(request, ORIGIN).href : request.url);
  const matchIn = (store) => async (request) => (store[keyOf(request)] ? new Response(store[keyOf(request)]) : undefined);
  const matchKept = async (request) => (await matchIn(kept)(request)) || matchIn(keptData)(request);
  const context = {
    self,
    location: self.location,
    URL,
    Response,
    console,
    fetch: async (request) => {
      fetched.push(typeof request === "string" ? request : request.url);
      if (network === "down") throw new TypeError("Failed to fetch");
      const url = typeof request === "string" ? request : request.url;
      return new Response("fresh", { status: 200, headers: headers[url] || {} });
    },
    caches: {
      match: matchKept,
      open: async (name) => ({
        match: matchIn(String(name).endsWith("-data") ? keptData : kept),
        put: async (request) => { stored.push(typeof request === "string" ? request : request.url); },
        add: async () => {}, addAll: async () => {},
        name,
      }),
      keys: async () => cacheNames,
      delete: async (name) => { deleted.push(name); return true; },
    },
  };
  vm.runInNewContext(SOURCE, context);

  // What the handler answers for one request: "browser" when it leaves the
  // request to the browser (no respondWith), else the Response it settles on.
  // A page the browser opens is a navigation; everything else is a script's
  // or an element's request (fetch() is "cors").
  async function ask(url, { destination = "", method = "GET", mode } = {}) {
    let answer = "browser";
    const how = mode || (destination === "document" || destination === "iframe" ? "navigate" : "cors");
    handlers.fetch({ request: { url, method, destination, mode: how }, respondWith: (promise) => { answer = promise; } });
    return answer === "browser" ? answer : await answer;
  }

  async function activate() {
    let done;
    handlers.activate({ waitUntil: (promise) => { done = promise; } });
    await done;
  }
  return { ask, activate, fetched, stored, deleted };
}

test("another host's script is left to the browser, never answered with nothing", async () => {
  const sw = worker();
  assert.equal(await sw.ask("https://static.cloudflareinsights.com/beacon.min.js", { destination: "script" }), "browser");
  assert.deepEqual(sw.fetched, []);
});

test("an image from the media host is kept for offline: offline with no copy, a network error", async () => {
  const sw = worker();
  const answer = await sw.ask("https://media.rasova.net/menu/paneer.jpg", { destination: "image" });
  assert.ok(answer instanceof Response, "answered with a Response");
  assert.equal(answer.type, "error");
});

test("an image already kept is served from the cache offline", async () => {
  const url = "https://media.rasova.net/menu/paneer.jpg";
  const sw = worker({ kept: { [url]: "kept" } });
  assert.equal(await (await sw.ask(url, { destination: "image" })).text(), "kept");
});

test("one of our hashed static files, not kept and offline: a network error, not undefined", async () => {
  const sw = worker();
  const answer = await sw.ask(`${ORIGIN}/static/js/core/app.0123456789ab.js`, { destination: "script" });
  assert.ok(answer instanceof Response);
  assert.equal(answer.type, "error");
});

test("online, a hashed static file comes from the network and pages still work", async () => {
  const sw = worker({ network: "up" });
  assert.equal(await (await sw.ask(`${ORIGIN}/static/js/core/app.0123456789ab.js`)).text(), "fresh");
  assert.equal(await (await sw.ask(`${ORIGIN}/tables/`, { destination: "document" })).text(), "fresh");
});

// The admin's address is a secret in production (ADMIN_URL), so the worker
// cannot skip it by name: the server marks every admin response
// X-Rasova-Offline: never, and nothing so marked is kept (27 Nov 2026).
test("a page the server marks not for offline is never kept; an app page still is", async () => {
  const admin = `${ORIGIN}/back-office-x9/tenants/tenant/`;
  const page = `${ORIGIN}/billing/`;
  const sw = worker({ network: "up", headers: { [admin]: { "X-Rasova-Offline": "never" } } });
  assert.equal(await (await sw.ask(admin, { destination: "document" })).text(), "fresh");
  assert.equal(await (await sw.ask(page, { destination: "document" })).text(), "fresh");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(sw.stored, [page]);
});

/* ---- a script's data (28 Nov 2026) ------------------------------------------
 * Kept like a page, an old answer came back as if live whenever a read
 * failed: a kitchen board showed old tickets with no "Sync Failed", and a
 * read with no copy got the billing page instead of data. */

const TICKETS = `${ORIGIN}/kitchen-data/`;
const OPTIONS = `${ORIGIN}/menu/item-modifiers/12/`;

test("offline, the kitchen's tickets are a network error, never an old copy", async () => {
  const sw = worker({ kept: { [TICKETS]: '{"kots": ["from an hour ago"]}' } });
  const answer = await sw.ask(TICKETS);
  assert.ok(answer instanceof Response);
  assert.equal(answer.type, "error", "the board says Check the connection, not old tickets");
});

test("offline, a script's read with nothing kept is a network error, not the billing page", async () => {
  const sw = worker({ kept: { [`${ORIGIN}/billing/`]: "<html>the billing page</html>" } });
  for (const url of [TICKETS, `${ORIGIN}/tables-data/`, `${ORIGIN}/thermal-receipt/9/`]) {
    const answer = await sw.ask(url);
    assert.equal(answer.type, "error", url);
  }
});

test("online, a script's answer is not kept unless the server marks it keep", async () => {
  const sw = worker({ network: "up" });
  assert.equal(await (await sw.ask(TICKETS)).text(), "fresh");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(sw.stored, []);
});

test("a dish's options, marked keep, are kept and handed back offline (billing works offline)", async () => {
  const online = worker({ network: "up", headers: { [OPTIONS]: { "X-Rasova-Offline": "keep" } } });
  assert.equal(await (await online.ask(OPTIONS)).text(), "fresh");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(online.stored, [OPTIONS]);

  const offline = worker({ keptData: { [OPTIONS]: '{"groups": []}' } });
  assert.equal(await (await offline.ask(OPTIONS)).text(), '{"groups": []}');
});

test("pages are still kept online and served offline, with the billing page for a kitchen page not kept", async () => {
  const online = worker({ network: "up" });
  await online.ask(`${ORIGIN}/kitchen/`, { destination: "document" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(online.stored, [`${ORIGIN}/kitchen/`]);

  const offline = worker({ kept: { [`${ORIGIN}/billing/`]: "billing page" } });
  assert.equal(await (await offline.ask(`${ORIGIN}/kitchen/`, { destination: "document" })).text(), "billing page");
});

test("an image on our own address is kept as before, not treated as data", async () => {
  const sw = worker({ network: "up" });
  await sw.ask(`${ORIGIN}/media/menu/paneer.jpg`, { destination: "image", mode: "no-cors" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(sw.stored, [`${ORIGIN}/media/menu/paneer.jpg`]);
});

test("a new version clears the old version's caches, the data kept beside pages with them", async () => {
  const sw = worker({ cacheNames: ["rasova-v8-static", "rasova-v8-menu", "rasova-v9-static", "rasova-v9-menu", "rasova-v9-data", "other-app"] });
  await sw.activate();
  assert.deepEqual(sw.deleted, ["rasova-v8-static", "rasova-v8-menu"]);
});
