/*
 * The printer chip's words (static/js/core/printing_status.js) and setup's
 * Test Print (static/js/setup/test_print.js), with fakes.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createPrinterChip, describePrinting } from "../core/printing_status.js";
import { createTestPrint } from "../setup/test_print.js";
import { networkDown, reply } from "./helpers.mjs";

const state = (extra = {}) => ({
  agent_seen_seconds: 5, waiting: 0, oldest_seconds: null, failed_recent: 0, last_error: "", ...extra,
});
const ANDROID = { android: true };
const DESKTOP = {};
const IPHONE = { ios: true };

/* ---- the chip's words ----------------------------------------------------- */

test("an agent that just checked in, nothing waiting: ready", () => {
  assert.deepEqual(describePrinting(state(), { platform: DESKTOP }),
    { level: "ok", text: "Printer ready", hint: "The print app is connected." });
});

test("jobs printing right now are not a fault", () => {
  const said = describePrinting(state({ waiting: 2, oldest_seconds: 20 }));
  assert.equal(said.level, "ok");
  assert.equal(said.text, "Printing 2");
});

test("an agent silent for more than two minutes: red, and what to open", () => {
  const phone = describePrinting(state({ agent_seen_seconds: 600 }), { platform: ANDROID });
  assert.deepEqual(phone, {
    level: "bad", text: "Print app not seen for 10 min",
    hint: "Open the Rasova app on this phone and keep it logged in.",
  });
  const desk = describePrinting(state({ agent_seen_seconds: 600 }), { platform: DESKTOP });
  assert.match(desk.hint, /counter phone, or start the print agent/);
  assert.equal(describePrinting(state({ agent_seen_seconds: 120 })).level, "ok", "two minutes exactly is still fine");
});

test("jobs stuck with the agent connected point at the printer, not the app", () => {
  const said = describePrinting(state({ waiting: 3, oldest_seconds: 400 }));
  assert.equal(said.level, "bad");
  assert.equal(said.text, "3 waiting to print (7 min)");
  assert.match(said.hint, /printer is not taking jobs/);
});

test("jobs stuck with no agent point at the app", () => {
  const said = describePrinting(state({ agent_seen_seconds: 900, waiting: 1, oldest_seconds: 900 }), { platform: ANDROID });
  assert.equal(said.hint, "Open the Rasova app on this phone and keep it logged in.");
});

test("a failure in the last hour says so, with the agent's reason", () => {
  assert.deepEqual(describePrinting(state({ failed_recent: 1, last_error: "Connection refused" })),
    { level: "warn", text: "Last print failed", hint: "Connection refused" });
});

test("no agent ever: grey, and on an iPhone the bill prints by AirPrint", () => {
  assert.equal(describePrinting(state({ agent_seen_seconds: null }), { platform: DESKTOP }).text, "No print app connected");
  assert.equal(describePrinting(state({ agent_seen_seconds: null }), { platform: DESKTOP }).level, "info");
  assert.equal(describePrinting(state({ agent_seen_seconds: null }), { platform: IPHONE }).text, "Prints from the bill");
});

test("no state, or not an object: nothing to say", () => {
  assert.equal(describePrinting(undefined), null);
  assert.equal(describePrinting("x"), null);
});

test("the chip shows the level, the words and the hint, and hides with nothing to say", () => {
  const classes = new Set();
  const text = { textContent: "" };
  const attrs = {};
  const el = {
    hidden: true, title: "",
    classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) },
    querySelector: () => text,
    setAttribute: (k, v) => { attrs[k] = v; },
  };
  const chip = createPrinterChip({ el, platform: ANDROID });
  chip.update(state({ agent_seen_seconds: 300 }));
  assert.equal(el.hidden, false);
  assert.deepEqual([...classes], ["is-bad"]);
  assert.equal(text.textContent, "Print app not seen for 5 min");
  assert.equal(el.title, "Open the Rasova app on this phone and keep it logged in.");
  chip.update(state());
  assert.deepEqual([...classes], ["is-ok"]);
  chip.update(undefined);
  assert.equal(el.hidden, true);
  assert.equal(createPrinterChip({ el: null }).update(state()), null, "a page with no chip is fine");
});

/* ---- Test Print ----------------------------------------------------------- */

const URLS = { test_print: "/setup/printer/test/", job_status: "/printing/job/0/" };

function rig({ post = async () => ({ success: true, job_id: 7 }), answers = [] } = {}) {
  const shown = [];
  let clock = 0;
  const asked = [];
  const testPrint = createTestPrint({
    api: { post },
    fetchFn: async (url) => {
      asked.push(url);
      const next = answers.shift();
      if (next instanceof Error) throw next;
      return next || reply(200, { status: "pending" });
    },
    urls: URLS,
    show: (level, words) => shown.push([level, words]),
    sleep: async (ms) => { clock += ms; },
    now: () => clock,
    every: 1500,
  });
  return { testPrint, shown, asked };
}

test("it waits for the printer, and says printed only when a printer took it", async () => {
  const { testPrint, shown, asked } = rig({ answers: [reply(200, { status: "pending" }), reply(200, { status: "processing" }), reply(200, { status: "done" })] });
  assert.equal(await testPrint.run("192.168.1.50"), "ok");
  assert.deepEqual(shown.map((s) => s[0]), ["wait", "wait", "ok"]);
  assert.equal(shown.at(-1)[1], "Printed. The printer took the test page.");
  assert.deepEqual([...new Set(asked)], ["/printing/job/7/"]);
});

test("a refusal shows the agent's reason", async () => {
  const { testPrint, shown } = rig({ answers: [reply(200, { status: "failed", error: "Connection refused" })] });
  assert.equal(await testPrint.run("192.168.1.50"), "bad");
  assert.match(shown.at(-1)[1], /The printer refused it: Connection refused/);
});

test("nobody taking it in twenty seconds points at the app; taken but silent points at the printer", async () => {
  const nobody = rig();
  assert.equal(await nobody.testPrint.run("192.168.1.50"), "bad");
  assert.match(nobody.shown.at(-1)[1], /Nothing picked it up in 20 seconds/);
  const silent = rig({ answers: Array(30).fill(null).map(() => reply(200, { status: "processing" })) });
  await silent.testPrint.run("192.168.1.50");
  assert.match(silent.shown.at(-1)[1], /took the page but the printer has not answered/);
});

test("a dropped connection while waiting is asked again, not a failure", async () => {
  const { testPrint } = rig({ answers: [networkDown(), reply(500, {}), reply(200, { status: "done" })] });
  assert.equal(await testPrint.run("192.168.1.50"), "ok");
});

test("no IP: nothing is sent", async () => {
  let posted = 0;
  const { testPrint, shown } = rig({ post: async () => { posted += 1; } });
  assert.equal(await testPrint.run("  "), "bad");
  assert.equal(posted, 0);
  assert.deepEqual(shown, [["bad", "Enter a printer IP and Save first."]]);
});

test("the server refusing the test shows its words", async () => {
  const { testPrint, shown } = rig({ post: async () => { throw new Error("Set a printer IP first, then test."); } });
  assert.equal(await testPrint.run("192.168.1.50"), "bad");
  assert.equal(shown.at(-1)[1], "Set a printer IP first, then test.");
});

test("a second tap while one is running is ignored", async () => {
  const { testPrint } = rig({ answers: [reply(200, { status: "done" })] });
  const [first, second] = await Promise.all([testPrint.run("1.2.3.4"), testPrint.run("1.2.3.4")]);
  assert.deepEqual([first, second], ["ok", null]);
});
