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

function worker({ network = "down", kept = {} } = {}) {
  const handlers = {};
  const self = {
    location: new URL(ORIGIN),
    addEventListener: (type, fn) => { handlers[type] = fn; },
    skipWaiting: async () => {},
    clients: { claim: async () => {} },
  };
  const fetched = [];
  const context = {
    self,
    location: self.location,
    URL,
    Response,
    console,
    fetch: async (request) => {
      fetched.push(typeof request === "string" ? request : request.url);
      if (network === "down") throw new TypeError("Failed to fetch");
      return new Response("fresh", { status: 200 });
    },
    caches: {
      match: async (request) => {
        const url = typeof request === "string" ? new URL(request, ORIGIN).href : request.url;
        return kept[url] ? new Response(kept[url]) : undefined;
      },
      open: async () => ({ put: async () => {}, add: async () => {}, addAll: async () => {} }),
      keys: async () => [],
      delete: async () => true,
    },
  };
  vm.runInNewContext(SOURCE, context);

  // What the handler answers for one request: "browser" when it leaves the
  // request to the browser (no respondWith), else the Response it settles on.
  async function ask(url, { destination = "", method = "GET" } = {}) {
    let answer = "browser";
    handlers.fetch({ request: { url, method, destination }, respondWith: (promise) => { answer = promise; } });
    return answer === "browser" ? answer : await answer;
  }
  return { ask, fetched };
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
