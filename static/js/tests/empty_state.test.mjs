/*
 * The empty state a script draws (static/js/core/empty_state.js) and the
 * floor plan of a restaurant with no tables yet.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { emptyStateHtml } from "../core/empty_state.js";
import { floorHtml } from "../tables/floor.js";

test("the words, the icon and one button, all escaped", () => {
  const html = emptyStateHtml({ icon: "inbox", title: "<b>None</b>", text: 'a "quote"', href: "/x/?a=1&b=2", label: "Add <it>" });
  assert.match(html, /data-empty-state/);
  assert.match(html, /&lt;b&gt;None&lt;\/b&gt;/);
  assert.match(html, /href="\/x\/\?a=1&amp;b=2"/);
  assert.match(html, />Add &lt;it&gt;</);
  assert.equal(html.match(/<a /g).length, 1);
});

test("no address, no button; no text, no paragraph", () => {
  const html = emptyStateHtml({ title: "Nothing" });
  assert.doesNotMatch(html, /<a |<p>/);
});

test("a floor with no tables offers to add them, and does not say All clear", () => {
  const html = floorHtml([], { setup_tables: "/setup/tables/" });
  assert.match(html, /No tables yet/);
  assert.match(html, /href="\/setup\/tables\/"/);
  assert.doesNotMatch(html, /All clear/);
});
