/*
 * Purchase orders (inventory/purchase_orders.html): the New / Edit drawer
 * and the Receive drawer. Order and Cancel are post buttons
 * (core/post_actions.js) asked in Rasova's own sheet: the browser's
 * confirm() never shows in the Android app. Item names are set as text.
 * Requests go through the shared client (core/app.js), which sends the
 * CSRF token and shows a refusal, instead of fetch() by hand. Data and
 * addresses come from inventory.views.purchase_orders_page_data.
 */
import { api, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { withId } from "../billing/urls.js";
import { poItems, poTotal, receiveBody, receivedWords, toReceive } from "./po_parts.js";

const data = pageData("page-data");
const $ = (id) => document.getElementById(id);
const ITEMS = data.items || [];
const LINES = data.lines || {};
const CREATE_LABEL = '<i class="bi bi-check-lg me-1"></i>Create Draft PO';
const SAVE_LABEL = '<i class="bi bi-check-lg me-1"></i>Save Changes';

let editing = null;
let receiving = null;
let busy = false;

function node(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function lineRow(prefill) {
  const row = node("div", "line-item-row");
  const select = node("select", "item-sel");
  select.dataset.change = "po.item";
  select.append(new Option("Item", ""));
  for (const i of ITEMS) {
    const option = new Option(`${i.name} (${i.unit})`, String(i.id));
    option.dataset.cost = String(i.cost);
    select.append(option);
  }
  const qty = node("input", "line-qty");
  Object.assign(qty, { type: "number", placeholder: "Qty", min: "0.001", step: "0.001" });
  const price = node("input", "line-price");
  Object.assign(price, { type: "number", placeholder: "₹", min: "0", step: "0.01" });
  const remove = node("button", "btn-icon btn-line-remove");
  remove.type = "button";
  remove.dataset.action = "po.removeLine";
  remove.setAttribute("aria-label", "Remove this item");
  remove.append(node("i", "bi bi-x"));
  row.append(select, qty, price, remove);
  if (prefill) {
    select.value = String(prefill.item_id);
    qty.value = prefill.quantity;
    price.value = prefill.unit_price;
  }
  $("lineItems").append(row);
}

function typedLines() {
  return Array.from(document.querySelectorAll("#lineItems .line-item-row")).map((row) => ({
    item_id: row.querySelector(".item-sel").value,
    quantity: row.querySelector(".line-qty").value,
    unit_price: row.querySelector(".line-price").value,
  }));
}

function recalc() {
  $("poTotal").textContent = poTotal(typedLines()).toFixed(2);
}

function showDrawer(open) {
  $("poDrawer").classList.toggle("open", open);
  $("drawerOverlay").hidden = !open;
}

function openNew() {
  editing = null;
  $("drawerTitleText").textContent = "New Purchase Order";
  $("po_supplier").disabled = false;
  $("po_supplier").value = "";
  $("po_notes").value = "";
  $("submitPoBtn").innerHTML = CREATE_LABEL;
  $("lineItems").replaceChildren();
  lineRow();
  recalc();
  showDrawer(true);
}

function openEdit(button) {
  const d = button.dataset;
  editing = Number(d.po);
  $("drawerTitleText").textContent = `Edit ${d.number}`;
  $("po_supplier").value = d.supplier;
  $("po_supplier").disabled = true;
  $("po_notes").value = d.notes || "";
  $("submitPoBtn").innerHTML = SAVE_LABEL;
  $("lineItems").replaceChildren();
  const lines = LINES[d.po] || [];
  lines.forEach((l) => lineRow({ item_id: l.item_id, quantity: l.quantity, unit_price: l.ordered_price }));
  if (!lines.length) lineRow();
  recalc();
  showDrawer(true);
}

function closeDrawer() {
  showDrawer(false);
  $("po_supplier").disabled = false;
  editing = null;
}

async function send(url, body, done) {
  if (busy) return;
  busy = true;
  try {
    const d = await api.post(url, body);
    ui.toast(done(d), "success");
    setTimeout(() => window.location.reload(), 700);
  } catch (shown) {
    busy = false;   // the client showed why
  }
}

function submitPO() {
  const checked = poItems(typedLines());
  if (checked.problem) {
    ui.toast(checked.problem, "error");
    return;
  }
  const notes = $("po_notes").value;
  if (editing) {
    send(withId(data.urls.edit, editing), { notes, items: checked.items }, () => "Purchase order updated.");
    return;
  }
  const supplierId = $("po_supplier").value;
  if (!supplierId) {
    ui.toast("Please select a supplier.", "error");
    return;
  }
  send(data.urls.create, { supplier_id: supplierId, notes, items: checked.items }, (d) => `PO ${d.po_number} created.`);
}

function openReceive(button) {
  const lines = toReceive(LINES[button.dataset.po]);
  if (!lines.length) {
    ui.toast("Nothing left to receive on this PO.", "error");
    return;
  }
  receiving = Number(button.dataset.po);
  const box = $("receiveLines");
  box.replaceChildren();
  for (const l of lines) {
    const row = node("div", "receive-line");
    row.dataset.itemId = String(l.item_id);
    const qty = node("input", "recv-qty");
    Object.assign(qty, { type: "number", min: "0", step: "0.001", value: l.remaining.toFixed(3) });
    const price = node("input", "recv-price");
    Object.assign(price, { type: "number", min: "0", step: "0.01", value: l.price.toFixed(2) });
    const qtyCell = node("div");
    qtyCell.append(node("div", "line-label", `Qty received now (of ${l.remaining.toFixed(3)} remaining)`), qty);
    const priceCell = node("div");
    priceCell.append(node("div", "line-label", "Price invoiced (₹)"), price);
    const grid = node("div", "receive-grid");
    grid.append(qtyCell, priceCell);
    row.append(node("div", "receive-name", l.name), grid);
    box.append(row);
  }
  $("receiveDrawer").classList.add("open");
  $("receiveOverlay").hidden = false;
}

function closeReceive() {
  $("receiveDrawer").classList.remove("open");
  $("receiveOverlay").hidden = true;
  receiving = null;
}

function submitReceive() {
  if (!receiving) return;
  const rows = Array.from(document.querySelectorAll("#receiveLines .receive-line")).map((row) => ({
    item_id: row.dataset.itemId,
    quantity: row.querySelector(".recv-qty").value,
    price: row.querySelector(".recv-price").value,
  }));
  send(withId(data.urls.receive, receiving), receiveBody(rows), (d) => receivedWords(d.status));
}

delegate(document.body, {
  "po.new": () => openNew(),
  "po.edit": (b) => openEdit(b),
  "po.close": () => closeDrawer(),
  "po.addLine": () => { lineRow(); recalc(); },
  "po.removeLine": (b) => { b.closest(".line-item-row").remove(); recalc(); },
  "po.submit": () => submitPO(),
  "po.receive": (b) => openReceive(b),
  "po.closeReceive": () => closeReceive(),
  "po.submitReceive": () => submitReceive(),
});

const lineBox = $("lineItems");
lineBox.addEventListener("change", (e) => {
  const select = e.target.closest(".item-sel");
  if (!select) return;
  const option = select.options[select.selectedIndex];
  if (option && option.dataset.cost) select.closest(".line-item-row").querySelector(".line-price").value = parseFloat(option.dataset.cost).toFixed(2);
  recalc();
});
lineBox.addEventListener("input", recalc);
