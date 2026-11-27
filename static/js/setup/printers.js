/*
 * Setup > Printers (setup/templates/setup/printers.html): each printer's
 * Save and Test print, Find printers (the PC agent's scan, cloud only) and
 * the MAC address field's formatting.
 *
 * Test print goes through the print queue (test_print.js), the same road
 * as a real bill: on a box the box prints it, in the cloud the PC agent
 * collects it. Each row waits for its own job's answer.
 * Addresses come from setup.views.printer_views.page_data.
 */
import { api, platform } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { withId } from "../billing/urls.js";
import { formatMac } from "./mac.js";
import { agentProblem, agentUrl, createAgent } from "./station_agent.js";
import { readPrinterForm, stateClass, unsaved } from "./printer_form.js";
import { createTestPrint } from "./test_print.js";

const data = pageData("page-data");
const card = (el) => el.closest("form[data-station]");

// Each row as last saved: Test print prints with that, not with what is typed.
const savedRows = new Map();
document.querySelectorAll("form[data-station]").forEach((form) => {
  savedRows.set(form.dataset.station, readPrinterForm(form));
});

function show(form, level, words) {
  const line = form.querySelector(".pr-result");
  line.className = "pr-result is-" + level;
  line.textContent = words;
}

const testers = new Map();
function tester(form) {
  const id = form.dataset.station;
  if (!testers.has(id)) {
    testers.set(id, createTestPrint({
      api,
      fetchFn: (...args) => fetch(...args),
      urls: { test_print: withId(data.urls.test, id), job_status: data.urls.job_status },
      show: (level, words) => show(form, level, words),
      box: Boolean(data.box),
    }));
  }
  return testers.get(id);
}

async function save(form, event) {
  event.preventDefault();
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  show(form, "wait", "Saving...");
  try {
    const sent = readPrinterForm(form);
    const answer = await api.post(withId(data.urls.save, form.dataset.station), sent);
    if (answer.printer_ip !== undefined) {
      sent.printer_ip = answer.printer_ip;        // as the server keeps it ("192.168.001.050" is 192.168.1.50)
      form.elements.printer_ip.value = answer.printer_ip;
    }
    savedRows.set(form.dataset.station, sent);
    show(form, "ok", answer.message || "Saved.");
    const state = form.querySelector(".pr-state");
    state.className = "pr-state " + stateClass(answer.level);
    state.textContent = answer.state;
    form.querySelector('[data-action="printer.test"]').disabled = !answer.printer_ip;
  } catch (refused) {
    show(form, "bad", refused.message || "Could not save. Check the address and try again.");
  } finally {
    button.disabled = false;
  }
}

async function test(button) {
  const form = card(button);
  if (unsaved(readPrinterForm(form), savedRows.get(form.dataset.station) || {})) {
    show(form, "bad", "Save your changes first: the test prints with what is saved.");
    return;
  }
  button.disabled = true;
  try {
    await tester(form).run(form.elements.printer_ip.value);
  } finally {
    button.disabled = false;
  }
}

let agent = null;
async function find(button) {
  const form = card(button);
  agent = agent || createAgent({
    WebSocketCtor: window.WebSocket,
    url: agentUrl(Boolean(platform && platform.android), data.agentHost),
  });
  button.disabled = true;
  show(form, "wait", "Looking for printers on this network...");
  try {
    const found = await agent.discover();
    if (!found.length) {
      show(form, "bad", "No printer answered. Check it is switched on and on the same network.");
    } else {
      form.elements.printer_ip.value = found[0].ip;
      const others = found.slice(1).map((p) => p.ip).join(", ");
      show(form, "ok", `Found ${found[0].ip}` + (others ? `; also ${others}` : "") + ". Press Save to keep it.");
    }
  } catch (err) {
    show(form, "bad", err && err.message === "not running"
      ? "Finding printers needs the Rasova agent running on a Windows PC on this network."
      : "The scan failed: " + agentProblem(err, "unknown"));
  } finally {
    button.disabled = false;
  }
}

delegate(document.body, {
  "printer.test": (b) => test(b),
  "printer.find": (b) => find(b),
});
delegate(document.body, { "printer.save": (form, event) => save(form, event) }, "submit");
delegate(document.body, {
  "mac.format": (input) => { input.value = formatMac(input.value); },
}, "input");
