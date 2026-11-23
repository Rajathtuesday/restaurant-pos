/*
 * Every pattern="..." in the templates is a valid regular expression the way
 * browsers read it now: with the `v` (unicodeSets) flag, where a "-" at the
 * end of a character class must be escaped. Chrome drops a pattern it can't
 * read, with only a console error, so the field silently stops checking:
 * onboarding's web address field did that (pattern="[a-z0-9-]+", found on
 * the walkthrough recording, 23 Nov 2026).
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { test } from "node:test";

const ROOT = new URL("../../../", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const SKIP = new Set([".git", ".venv", "node_modules", "md_files", "staticfiles", "public", "graft"]);

function templates(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (SKIP.has(name)) continue;
    const path = join(dir, name);
    const info = statSync(path);
    if (info.isDirectory()) templates(path, out);
    else if (name.endsWith(".html") && path.includes("templates")) out.push(path);
  }
  return out;
}

test("every pattern attribute compiles as the browser compiles it", () => {
  const found = [];
  for (const file of templates(ROOT)) {
    for (const m of readFileSync(file, "utf8").matchAll(/\bpattern="([^"]*)"/g)) {
      if (m[1].includes("{")) {
        if (m[1].includes("{{") || m[1].includes("{%")) continue;   // filled in by Django
      }
      found.push([relative(ROOT, file), m[1]]);
    }
  }
  assert.ok(found.length > 0, "the templates do have patterns");
  for (const [file, pattern] of found) {
    assert.doesNotThrow(() => new RegExp(`^(?:${pattern})$`, "v"), `${file}: pattern="${pattern}"`);
  }
});

test("the web address field still means what it meant", () => {
  const re = /^(?:[a-z0-9\-]+)$/v;
  assert.ok(re.test("copper-tap"));
  assert.ok(!re.test("Copper Tap"));
});
