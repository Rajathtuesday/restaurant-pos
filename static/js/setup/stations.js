/*
 * Setup, Kitchen Stations (setup/templates/setup/setup_kitchen_stations.html):
 * open a station, "made as soon as sent", remove a station, and the print
 * mode. A station's printer (its address, paper and test print) is on
 * Setup > Printers (printers.js) since 22 Nov 2026: here it is one line
 * with a link. Switching to One Printer and removing a station ask in
 * Rasova's own sheet: the browser's confirm() never shows in the Android
 * app, so neither could be done there. Data and addresses come from
 * setup.views.core_views.stations_page_data.
 */
import { api, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { withId } from "../billing/urls.js";

const data = pageData("page-data");
const $ = (id) => document.getElementById(id);
const GREEN = "#10b981";
const MUTED = "var(--text-muted)";

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
    hint("Give each station its own printer on Setup › Printers.", MUTED);
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
  "station.toggle": (el, event) => { if (!event.target.closest("form, a")) togglePanel(station(el)); },
  "station.remove": (b) => removeStation(b),
});
delegate(document.body, { "station.madeOnSend": (box) => setMadeOnSend(box) }, "change");

if (data.anyStationHasPrinter) {
  hint("Each station has its own printer. KOTs print at the station when the order is sent.", "");
} else {
  hint("✓ One Printer mode: only the ", GREEN, { name: data.defaultStationName || "default", after: " station needs a printer." });
}
