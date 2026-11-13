/*
 * The floor plan: every table as a card, refreshed every few seconds, and
 * the sheets around it (More, Combine, Transfer, Tables, a table's QR).
 *
 * The page gives this module its addresses in one json_script block,
 * page-data (orders.views.table_views.tables_page_data). Buttons carry
 * data-action; this is the one place that handles them.
 */
import { api, ui } from "../core/app.js";
import { delegate, onReady } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { createTableActions } from "./actions.js";
import { failedHtml, floorHtml, manageRows, menuLink, moreSheet, pickerOptions, sheetsKey, statusOf, summarise, addresses } from "./floor.js";
import { createFloorLoader } from "./loader.js";

const data = pageData("page-data");
const urls = data.urls;
const el = (id) => document.getElementById(id);
const sheet = (id) => window.bootstrap.Modal.getOrCreateInstance(el(id));
const SHEETS = ["actionModal", "mergeModal", "transferModal"];
const PICKERS = ["mergePrimary", "mergeSecondary", "transferFrom", "transferTo"];

/* ---- drawing -------------------------------------------------------------- */

let drawnFloor = null;
let drawnSheets = null;

function drawSheets(tables) {
  const key = sheetsKey(tables);
  if (key === drawnSheets) return;         // unchanged: leave what is being typed or picked alone
  drawnSheets = key;
  const options = pickerOptions(tables);
  PICKERS.forEach((id) => {
    const select = el(id);
    const chosen = select.value;
    select.innerHTML = options[id];
    if (chosen) select.value = chosen;
    if (select.selectedIndex < 0) select.selectedIndex = 0;   // the table chosen is no longer on offer
  });
  el("tblManageList").innerHTML = manageRows(tables);
}

function draw(tables) {
  const html = floorHtml(tables, urls);
  if (html !== drawnFloor) {
    drawnFloor = html;
    el("tableGrid").innerHTML = html;
  }
  const sum = summarise(tables);
  el("stat-total").textContent = sum.total;
  el("stat-free").textContent = sum.free;
  el("stat-active").textContent = sum.active;
  el("stat-billing").textContent = sum.billing;
  drawSheets(tables);
}

const floor = createFloorLoader({
  fetchFn: (...args) => fetch(...args),
  url: urls.tables_data,
  onTables: draw,
  onFailure: (message) => {
    drawnFloor = null;
    el("tableGrid").innerHTML = failedHtml(message);
  },
});

const actions = createTableActions({
  api, ui, urls,
  refresh: () => floor.refresh(),
  go: (url) => { window.location.href = url; },
  closeSheets: () => SHEETS.forEach((id) => sheet(id).hide()),
});

/* ---- the sheets ----------------------------------------------------------- */

function openMore(tableId) {
  const table = floor.find(tableId);
  if (!table) return;
  if (statusOf(table) === "free") {
    window.location.href = addresses(urls).billing(table.id);
    return;
  }
  const { note, buttons } = moreSheet(table, urls);
  el("actionModalTitle").textContent = table.name;
  el("actionModalStatus").textContent = note;
  el("actionModalButtons").innerHTML = buttons;
  sheet("actionModal").show();
}

function openTransfer(tableId) {
  sheet("actionModal").hide();
  el("transferFrom").value = String(tableId);
  sheet("transferModal").show();
}

/* A table's QR, drawn here by the page's own QR library: the menu link
   carries the table's token, which is nobody else's to see. */
function qrCanvas(text, width) {
  return new Promise((resolve) => {
    try {
      window.QRCode.toCanvas(text, { width, margin: 1, color: { dark: "#000000", light: "#ffffff" } },
        (err, canvas) => resolve(err ? null : canvas));
    } catch (noLibrary) {
      resolve(null);
    }
  });
}

let qrLink = "";

async function openQr(tableId) {
  const table = floor.find(tableId);
  if (!table) return;
  qrLink = menuLink(window.location.origin, urls.digital_menu, table.qr_token);
  el("qrModalTitle").textContent = table.name + " QR Reference";
  const box = el("qrBox");
  box.innerHTML = "";
  const canvas = await qrCanvas(qrLink, 300);
  if (canvas) box.appendChild(canvas);
  el("qrFailed").classList.toggle("is-shown", !canvas);
  sheet("manageTablesModal").hide();
  sheet("qrModal").show();
}

async function openQrLarge() {
  const canvas = await qrCanvas(qrLink, 900);
  if (!canvas) {
    ui.toast("Could not draw the QR code.", "error");
    return;
  }
  canvas.toBlob((image) => {
    const opened = image && window.open(URL.createObjectURL(image), "_blank");
    if (!opened) ui.toast("Allow pop-ups to see the large QR code.", "warning");
  });
}

/* ---- the actions ---------------------------------------------------------- */

const row = (button) => button.closest("[data-row]");

delegate(document.body, {
  "table.go": (button) => { window.location.href = button.dataset.url; },
  "table.more": (button) => openMore(button.dataset.id),
  "table.bill": (button) => actions.bill(button.dataset.order),
  "table.clean": (button) => actions.clean(button.dataset.id),
  "table.unmerge": (button) => actions.unmerge(button.dataset.id),
  "table.approve": (button) => actions.approve(button.dataset.order),
  "table.transfer": (button) => openTransfer(button.dataset.id),
  "merge.run": () => actions.merge(el("mergePrimary").value, el("mergeSecondary").value),
  "transfer.run": () => actions.transfer(el("transferFrom").value, el("transferTo").value, floor.tables()),
  "manage.create": async () => {
    const added = await actions.create(el("newTableName").value, el("newTableSection").value);
    if (added) {
      el("newTableName").value = "";
      el("newTableSection").value = "";
    }
  },
  "manage.save": (button) => actions.save(
    button.dataset.id,
    row(button).querySelector('[data-field="name"]').value,
    row(button).querySelector('[data-field="section"]').value,
  ),
  "manage.qr": (button) => openQr(button.dataset.id),
  "qr.large": () => openQrLarge(),
});

/* ---- go ------------------------------------------------------------------- */

onReady(() => {
  floor.refresh();
  setInterval(() => floor.refresh(), data.poll_ms || 5000);
});
