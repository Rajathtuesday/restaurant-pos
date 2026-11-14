/*
 * Setup, Promos (setup/templates/setup/setup_promos.html): Edit fills the
 * form with a promo (the page's promo-edit-data block), Cancel edit empties
 * it, Save sends it (promo_form.js says what and whether). Pause and
 * Delete are post buttons (core/post_actions.js); the switch "Run on All
 * Outlets" hides the outlet list.
 */
import { api } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { FIELDS, promoPayload, promoProblem } from "./promo_form.js";

const EDIT = pageData("promo-edit-data");
const CREATE_LABEL = '<i class="bi bi-plus-lg" aria-hidden="true"></i> Create Campaign';
const SAVE_LABEL = '<i class="bi bi-check-lg" aria-hidden="true"></i> Save changes';
const el = (id) => document.getElementById(id);
let editingId = null;

function showOutletPicker() {
  const toggle = el("all-outlets-toggle");
  const picker = el("outlet-picker");
  if (toggle && picker) picker.hidden = toggle.checked;
}

function say(text, ok) {
  const msg = el("form-msg");
  msg.hidden = false;
  msg.dataset.tone = ok ? "ok" : "bad";
  msg.textContent = text;
}

function edit(id) {
  const data = EDIT[id];
  if (!data) return;
  editingId = id;
  for (const [key, field] of Object.entries(FIELDS)) el(field).value = data[key] ?? "";
  el("form-title").textContent = `Edit: ${data.name}`;
  el("create-btn").innerHTML = SAVE_LABEL;
  el("cancel-edit-btn").hidden = false;
  if (el("scope-row")) el("scope-row").hidden = true;      // where it runs stays as it was made
  if (el("outlet-picker")) el("outlet-picker").hidden = true;
  el("f-name").focus();
}

function cancelEdit() {
  editingId = null;
  for (const field of Object.values(FIELDS)) el(field).value = "";
  el("f-type").value = "percentage";
  el("f-min").value = "0";
  el("form-title").textContent = "New Campaign";
  el("create-btn").innerHTML = CREATE_LABEL;
  el("cancel-edit-btn").hidden = true;
  if (el("scope-row")) el("scope-row").hidden = false;
  showOutletPicker();
}

async function save(button) {
  const allToggle = el("all-outlets-toggle");
  const outlet = el("f-outlet");
  const payload = promoPayload((key) => el(FIELDS[key]).value, {
    editing: editingId !== null,
    allOutlets: Boolean(allToggle && allToggle.checked),
    outletId: outlet ? outlet.value : null,
  });
  const problem = promoProblem(payload);
  if (problem) {
    say(problem, false);
    return;
  }
  const editing = editingId !== null;
  button.disabled = true;
  button.textContent = editing ? "Saving…" : "Creating…";
  try {
    const url = editing ? button.dataset.updateUrl.replace("/0/", `/${editingId}/`) : button.dataset.createUrl;
    const data = await api.post(url, payload);
    say(editing ? `"${data.name}" saved.` : `"${data.name}" created for ${data.scope}.`, true);
    setTimeout(() => window.location.reload(), 900);
  } catch (err) {
    if (err && err.message && err.message !== "Offline") say(err.message, false);   // also in a toast
  } finally {
    button.disabled = false;
    button.innerHTML = editing ? SAVE_LABEL : CREATE_LABEL;
  }
}

delegate(document.body, {
  "promo.edit": (button) => edit(button.dataset.promo),
  "promo.cancel": () => cancelEdit(),
  "promo.save": (button) => save(button),
});
delegate(document.body, { "promo.scope": () => showOutletPicker() }, "change");
