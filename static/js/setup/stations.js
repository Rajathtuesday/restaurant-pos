/*
 * Setup, Kitchen Stations (setup/templates/setup/setup_kitchen_stations.html):
 * open a station, save its printer, Test Print and scan through the Rasova
 * agent (station_agent.js), "made as soon as sent", remove a station, and
 * the print mode. Switching to One Printer and removing a station ask in
 * Rasova's own sheet: the browser's confirm() never shows in the Android
 * app, so neither could be done there. What the agent or a person typed is
 * shown as text. Data and addresses come from
 * setup.views.core_views.stations_page_data.
 */
import { api, platform, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { withId } from "../billing/urls.js";
import { agentProblem, agentUrl, createAgent } from "./station_agent.js";

const data = pageData("page-data");
const $ = (id) => document.getElementById(id);
const GREEN = "#10b981";
const RED = "#ef4444";
const MUTED = "var(--text-muted)";
const COLOURS = { ok: GREEN, err: RED, busy: MUTED };

const agent = createAgent({
  WebSocketCtor: window.WebSocket,
  url: agentUrl(Boolean(platform && platform.android), data.agentHost),
});

function setStatus(id, type, message) {
  const el = $(`status-${id}`);
  const dot = document.createElement("span");
  dot.className = "dot";
  dot.style.background = COLOURS[type];
  el.replaceChildren(dot, document.createTextNode(" " + message));
}

function firstDot(id, colour) {
  const dot = $(`card-${id}`).querySelector(".dot");
  if (dot) dot.style.background = colour;
}

async function checkAgent() {
  const dot = $("agentDot");
  const text = $("agentStatusText");
  try {
    await agent.ping();
    dot.className = "dot green";
    text.textContent = "Rasova Agent running";
    text.style.color = GREEN;
  } catch (notRunning) {
    dot.className = "dot red";
    const link = document.createElement("a");
    link.href = data.urls.installer;
    link.setAttribute("download", "");
    link.className = "agent-download";
    link.textContent = "Download installer";
    text.replaceChildren(document.createTextNode("Agent not running. "), link);
    text.style.color = MUTED;
  }
}

function hint(words, colour, strong) {
  const el = $("printModeHint");
  el.replaceChildren(document.createTextNode(words));
  if (strong) {
    const b = document.createElement("strong");
    b.textContent = strong.name;
    el.append(b, document.createTextNode(strong.after));
  }
  el.style.color = colour;
}

function showMode(mode) {
  $("modeCashier").classList.toggle("active", mode === "cashier");
  $("modeStation").classList.toggle("active", mode === "station");
}

async function setPrintMode(mode) {
  if (mode !== "cashier") {
    hint("Enter a printer IP for each station below.", MUTED);
    showMode("station");
    return;
  }
  const answer = await ui.confirm(
    "Switch to One Printer mode?",
    "This clears the printer IP from every station except the default one. The default station keeps its IP: bill and KOTs print there as one strip.",
    "Switch",
  );
  if (!answer || !answer.isConfirmed) return;
  try {
    await api.post(data.urls.cashierMode, {});
  } catch (shown) {
    return;   // the client showed why
  }
  hint("✓ One Printer mode active.", GREEN);
  showMode("cashier");
  setTimeout(() => window.location.reload(), 800);
}

function togglePanel(id) {
  const open = !$(`panel-${id}`).classList.contains("open");
  $(`panel-${id}`).classList.toggle("open", open);
  $(`chevron-${id}`).classList.toggle("open", open);
}

function toggleAdvanced(id) {
  const open = !$(`adv-${id}`).classList.contains("open");
  $(`adv-${id}`).classList.toggle("open", open);
  $(`adv-icon-${id}`).className = open ? "bi bi-chevron-down adv-icon" : "bi bi-chevron-right adv-icon";
}

async function saveConfig(id) {
  const ip = $(`ip-${id}`).value.trim();
  setStatus(id, "busy", "Saving…");
  try {
    const d = await api.post(withId(data.urls.printer, id), {
      printer_ip: ip, printer_port: $(`port-${id}`).value.trim(),
      paper_width_mm: $(`paper-${id}`).value, cut_type: $(`cut-${id}`).value, printer_encoding: $(`enc-${id}`).value,
    });
    setStatus(id, "ok", d.message || "Saved");
    const testBtn = $(`test-btn-${id}`);
    if (ip) {
      testBtn.disabled = false;
      testBtn.title = "";
    }
    firstDot(id, ip ? "#f59e0b" : "var(--border-color)");
  } catch (refused) {
    setStatus(id, "err", "Save failed");
  }
}

async function testPrint(id, btn) {
  if (btn.disabled) return;
  const ip = $(`ip-${id}`).value.trim();
  const port = parseInt($(`port-${id}`).value || "9100", 10);
  if (!ip) {
    setStatus(id, "err", "Enter a printer IP first");
    return;
  }
  btn.disabled = true;
  setStatus(id, "busy", "Connecting to agent…");
  try {
    await agent.testPrint(id, ip, port);
    setStatus(id, "ok", "Printed!");
    firstDot(id, GREEN);
  } catch (err) {
    const message = agentProblem(err, "Could not reach printer");
    setStatus(id, "err", message);
    ui.toast(message, "error");
  } finally {
    btn.disabled = false;
  }
}

function result(id, words, colour) {
  const el = $(`discover-result-${id}`);
  el.replaceChildren(document.createTextNode(words));
  el.style.color = colour;
  return el;
}

async function discover(id, btn) {
  if (btn.disabled) return;
  btn.disabled = true;
  result(id, "Scanning network…", MUTED);
  try {
    const found = await agent.discover();
    if (!found.length) {
      result(id, "No printers found on network", RED);
    } else if (found.length === 1) {
      $(`ip-${id}`).value = found[0].ip;
      result(id, `✓ Found: ${found[0].ip} (${found[0].responded_ms}ms)`, GREEN);
    } else {
      const el = result(id, `Found ${found.length}: `, MUTED);
      found.forEach((printer, i) => {
        if (i) el.append(document.createTextNode(" · "));
        const pick = document.createElement("button");
        pick.type = "button";
        pick.className = "ip-pick";
        pick.dataset.action = "station.pickIp";
        pick.dataset.station = String(id);
        pick.dataset.ip = String(printer.ip);
        pick.textContent = String(printer.ip);
        el.append(pick);
      });
    }
  } catch (err) {
    result(id, err && err.message === "not running" ? "Rasova Agent not running" : "Scan failed: " + agentProblem(err, "unknown"), RED);
  } finally {
    btn.disabled = false;
  }
}

async function setMadeOnSend(box) {
  try {
    await api.post(withId(data.urls.madeOnSend, box.dataset.station), { on: box.checked });
    ui.toast(box.checked ? "Drinks from this station now count as made when sent."
      : "Back to the kitchen rule (made after 10 minutes).", "success");
  } catch (refused) {
    box.checked = !box.checked;
  }
}

async function removeStation(btn) {
  const name = btn.dataset.name;
  const answer = btn.dataset.default === "true"
    ? await ui.confirm(`Remove "${name}"?`, "It is the default station. Another station becomes the default.", "Remove")
    : await ui.confirm(`Remove "${name}"?`, "Dishes sent to it move to the default station.", "Remove");
  if (!answer || !answer.isConfirmed) return;
  btn.disabled = true;
  try {
    const d = await api.post(withId(data.urls.remove, btn.dataset.station), {});
    ui.toast(d.message || "Station removed", "success");
    window.location.reload();
  } catch (refused) {
    btn.disabled = false;
  }
}

const station = (el) => Number(el.dataset.station);

delegate(document.body, {
  "station.mode": (b) => setPrintMode(b.dataset.mode),
  "station.toggle": (el, event) => { if (!event.target.closest("form")) togglePanel(station(el)); },
  "station.advanced": (b) => toggleAdvanced(station(b)),
  "station.save": (b) => saveConfig(station(b)),
  "station.test": (b) => testPrint(station(b), b),
  "station.discover": (b) => discover(station(b), b),
  "station.pickIp": (b) => {
    $(`ip-${b.dataset.station}`).value = b.dataset.ip;
    result(b.dataset.station, `Selected ${b.dataset.ip}`, GREEN);
  },
  "station.remove": (b) => removeStation(b),
});
delegate(document.body, { "station.madeOnSend": (box) => setMadeOnSend(box) }, "change");

checkAgent();
if (data.anyStationHasPrinter) {
  hint("Each station has its own printer. KOTs print at the station when the order is sent.", "");
} else {
  hint("✓ One Printer mode: enter the IP on the ", GREEN, { name: data.defaultStationName || "default", after: " station only." });
}
