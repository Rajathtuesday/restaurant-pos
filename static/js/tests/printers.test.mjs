/*
 * Setup > Printers (static/js/setup/printers.js): what one printer's row
 * sends, and what a test print says on a Rasova box, where the box prints
 * its own queue and no phone app is involved.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { PRINTER_FIELDS, readPrinterForm, stateClass } from "../setup/printer_form.js";
import { createTestPrint } from "../setup/test_print.js";
import { reply } from "./helpers.mjs";

function form(values) {
  const elements = {};
  for (const [name, value] of Object.entries(values)) elements[name] = { value };
  return { elements };
}

test("a printer's row sends its fields, trimmed", () => {
  const sent = readPrinterForm(form({
    printer_ip: " 192.168.1.60 ", printer_port: "9100", paper_width_mm: "58", cut_type: "partial",
    printer_encoding: "cp437", printer_mac: "00:57:27:10:ED:46", printer_name: "BillTouch",
  }));
  assert.deepEqual(sent, {
    printer_ip: "192.168.1.60", printer_port: "9100", paper_width_mm: "58", cut_type: "partial",
    printer_encoding: "cp437", printer_mac: "00:57:27:10:ED:46", printer_name: "BillTouch",
  });
});

test("a field the row doesn't have is not sent, so the server leaves it as it was", () => {
  const sent = readPrinterForm(form({ printer_ip: "192.168.1.61", printer_port: "9100" }));
  assert.deepEqual(Object.keys(sent), ["printer_ip", "printer_port"]);
  assert.ok(!("printer_name" in sent), "a box's row has no USB printer name: sending '' would wipe it");
  assert.ok(PRINTER_FIELDS.includes("printer_mac"));
});

test("the state line's class is one the page styles, whatever the server says", () => {
  assert.equal(stateClass("bad"), "is-bad");
  assert.equal(stateClass("ok"), "is-ok");
  assert.equal(stateClass("<img>"), "is-none");
  assert.equal(stateClass(undefined), "is-none");
});

function rig({ box, answers }) {
  const shown = [];
  let clock = 0;
  const testPrint = createTestPrint({
    api: { post: async () => ({ success: true, job_id: 3 }) },
    fetchFn: async () => answers.shift() || reply(200, { status: "pending" }),
    urls: { test_print: "/setup/printers/4/test/", job_status: "/printing/job/0/" },
    show: (level, words) => shown.push([level, words]),
    box,
    sleep: async (ms) => { clock += ms; },
    now: () => clock,
  });
  return { testPrint, shown };
}

test("on a box, a page nobody printed points at the box, never at a phone app", async () => {
  const { testPrint, shown } = rig({ box: true, answers: [] });
  assert.equal(await testPrint.run("192.168.1.60"), "bad");
  assert.match(shown.at(-1)[1], /The box did not print it/);
  assert.doesNotMatch(shown.at(-1)[1], /phone/);
});

test("on a box, a page sent but not answered points at the printer and the box's network", async () => {
  const { testPrint, shown } = rig({ box: true, answers: Array(30).fill(null).map(() => reply(200, { status: "processing" })) });
  await testPrint.run("192.168.1.60");
  assert.match(shown.at(-1)[1], /same network as the box/);
});

test("in the cloud, nobody picking it up still points at the counter phone", async () => {
  const { testPrint, shown } = rig({ box: false, answers: [] });
  await testPrint.run("192.168.1.60");
  assert.match(shown.at(-1)[1], /Open the Rasova app on the counter phone/);
});
