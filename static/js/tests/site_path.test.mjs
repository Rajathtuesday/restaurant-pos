/*
 * Addresses taken from the page are only ever paths on this site
 * (static/js/core/site_path.js; CodeQL js/xss-through-dom, 21 Nov 2026).
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { renderOrderHeader } from "../billing/tokens.js";
import { goTo, sitePath } from "../core/site_path.js";

test("a path on this site passes; anything that could run script or leave the site does not", () => {
  for (const good of ["/bill/7/", "/tables/", "/thermal-receipt/12/?print=1"]) {
    assert.equal(sitePath(good), good);
  }
  for (const bad of ["javascript:alert(1)", " javascript:alert(1)", "//evil.example/x", "/\\evil.example",
                     "https://evil.example/", "data:text/html,x", "", null, undefined, "bill/7/"]) {
    assert.equal(sitePath(bad), null, String(bad));
  }
});

test("goTo goes only to a path on this site", () => {
  const win = { location: { href: "/start/" } };
  assert.equal(goTo("javascript:alert(1)", win), false);
  assert.equal(win.location.href, "/start/");
  assert.equal(goTo("/bill/7/", win), true);
  assert.equal(win.location.href, "/bill/7/");
});

test("the token screen's Pay Now and Print buttons never carry a hostile address or id", () => {
  const els = {};
  const doc = { getElementById: (id) => (els[id] ||= { textContent: "", innerHTML: "" }) };
  renderOrderHeader({ order_status: "billing", remaining: 100 }, 7, { doc, billUrl: "javascript:alert(1)//" });
  assert.ok(!els.orderActionButtonDesktop.innerHTML.includes("javascript:"), els.orderActionButtonDesktop.innerHTML);
  renderOrderHeader({ order_status: "closed" }, '7" onclick="boom()', { doc, billUrl: "/bill/0/" });
  assert.ok(!els.orderActionButtonDesktop.innerHTML.includes('" onclick="'), els.orderActionButtonDesktop.innerHTML);
  renderOrderHeader({ order_status: "billing", remaining: 100 }, 7, { doc, billUrl: "/bill/0/" });
  assert.ok(els.orderActionButtonDesktop.innerHTML.includes('href="/bill/7/"'), els.orderActionButtonDesktop.innerHTML);
});
