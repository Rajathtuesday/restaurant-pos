/*
 * Box health follows a pen drive update (static/js/box/update_watch.js):
 * it keeps asking while Rasova restarts, and reloads when the state moves.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { EVERY_MS, GIVE_UP_MS, createUpdateWatch } from "../box/update_watch.js";
import { reply } from "./helpers.mjs";

function rig(answers, startState = "installing") {
  let clock = 0;
  let reloaded = 0;
  const shown = [];
  const watch = createUpdateWatch({
    fetchFn: async () => {
      const next = answers.shift();
      if (next instanceof Error) throw next;
      return next || reply(200, { state: startState, working: true, message: "Installing" });
    },
    url: "/box/update-status/",
    startState,
    reload: () => { reloaded += 1; },
    show: (words) => shown.push(words),
    sleep: async (ms) => { clock += ms; },
    now: () => clock,
  });
  return { watch, shown, reloaded: () => reloaded, clock: () => clock };
}

test("Rasova restarting mid-install is expected: it keeps asking, then reloads on done", async () => {
  const down = new TypeError("Failed to fetch");
  const r = rig([reply(200, { state: "installing", working: true, message: "Installing" }), down, down, reply(502, {}),
    reply(200, { state: "done", working: false, message: "Rasova 1.4.2 is running." })]);
  assert.equal(await r.watch.run(), "done");
  assert.equal(r.reloaded(), 1);
  assert.ok(r.shown.some((w) => /restarting with the new version/.test(w)));
});

test("a scan that finds the update reloads to show the Install button", async () => {
  const r = rig([reply(200, { state: "found", working: false })], "scanning");
  assert.equal(await r.watch.run(), "found");
  assert.equal(r.reloaded(), 1);
});

test("asked, then picked up by the box, reloads as soon as the state moves", async () => {
  const r = rig([reply(200, { state: "asked", working: true }), reply(200, { state: "scanning", working: true })], "asked");
  assert.equal(await r.watch.run(), "scanning");
});

test("an update the box never finishes stops being asked after half an hour", async () => {
  const r = rig([]);
  assert.equal(await r.watch.run(), null);
  assert.equal(r.reloaded(), 0);
  assert.ok(r.clock() >= GIVE_UP_MS && EVERY_MS > 0);
  assert.match(r.shown.at(-1), /hasn't finished/);
});
