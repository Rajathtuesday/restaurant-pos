import assert from "node:assert/strict";
import { test } from "node:test";

import { csrfToken } from "../core/csrf.js";
import { pageData } from "../core/page_data.js";
import { detectPlatform, startAndroidPrinting } from "../core/platform.js";

const CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36";
const CHROME_ANDROID = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36";
const IPAD = "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1";

test("platform: desktop, phone, tablet, and the Rasova app", () => {
  const win = detectPlatform({ userAgent: CHROME_WIN, standalone: false, agentHost: "192.168.1.20" });
  assert.deepEqual(
    [win.windows, win.android, win.ios, win.mac, win.nativeAndroid, win.agentHost],
    [true, false, false, false, false, "192.168.1.20"],
  );

  const android = detectPlatform({ userAgent: CHROME_ANDROID, standalone: true, agentHost: "192.168.1.20" });
  assert.equal(android.android, true);
  assert.equal(android.agentHost, "localhost", "on Android the agent runs on the phone itself");
  assert.equal(android.standalone, true);

  const app = detectPlatform({ userAgent: CHROME_ANDROID + " RasovaPOS-Android/1.2", standalone: false });
  assert.equal(app.nativeAndroid, true);

  const ipad = detectPlatform({ userAgent: IPAD, standalone: false });
  assert.equal(ipad.ios, true);
  assert.equal(ipad.mac, false, "an iPad says 'like Mac OS X' and is not a Mac");
});

test("the Android app is handed the server and the agent key through the bridge", () => {
  const given = [];
  const bridge = { startPrintingWithKey: (url, key) => given.push([url, key]) };
  const platform = detectPlatform({ userAgent: "x RasovaPOS-Android/1.2", standalone: false });
  const started = startAndroidPrinting({ platform, serverUrl: "https://spice.rasova.net", agentKey: "k1", bridge });
  assert.equal(started, true);
  assert.deepEqual(given, [["https://spice.rasova.net", "k1"]]);
});

test("an old app without the keyed bridge is told to update, once per session", () => {
  const toasts = [];
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) || null, setItem: (k, v) => store.set(k, v) };
  const platform = detectPlatform({ userAgent: "x RasovaPOS-Android/1.0", standalone: false });
  const ui = { toast: (m) => toasts.push(m) };
  const warn = console.warn;
  console.warn = () => {};
  try {
    startAndroidPrinting({ platform, serverUrl: "u", agentKey: "k", bridge: {}, ui, storage });
    startAndroidPrinting({ platform, serverUrl: "u", agentKey: "k", bridge: {}, ui, storage });
  } finally {
    console.warn = warn;
  }
  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /Update the Rasova app/);
});

test("in a browser nothing is started", () => {
  const platform = detectPlatform({ userAgent: CHROME_WIN, standalone: false });
  assert.equal(startAndroidPrinting({ platform, serverUrl: "u", agentKey: "k", bridge: undefined }), false);
});

test("the CSRF token comes from the meta tag, or the cookie without one", () => {
  const withMeta = { querySelector: () => ({ getAttribute: () => "meta-token" }), cookie: "csrftoken=cookie-token" };
  assert.equal(csrfToken(withMeta), "meta-token");
  const withoutMeta = { querySelector: () => null, cookie: "a=b; csrftoken2=cookie%2Dtoken; c=d" };  // settings.CSRF_COOKIE_NAME
  assert.equal(csrfToken(withoutMeta), "cookie-token");
  assert.equal(csrfToken({ querySelector: () => null, cookie: "" }), "");
});

test("page data is the JSON block, or an empty object", () => {
  const doc = { getElementById: (id) => (id === "rasova-data" ? { textContent: '{"authenticated": true, "agent_host": "10.0.0.5"}' } : null) };
  assert.deepEqual(pageData("rasova-data", doc), { authenticated: true, agent_host: "10.0.0.5" });
  assert.deepEqual(pageData("missing", doc), {});
  assert.deepEqual(pageData("rasova-data", { getElementById: () => ({ textContent: "not json" }) }), {});
});
