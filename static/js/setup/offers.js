/*
 * Setup, Offers (setup/templates/setup/setup_offers.html): pick a kind of
 * offer, fill the form, watch the example bill, save. Edit loads an offer
 * back into the form (page-data "forms"). Pause, Remove and Pause all are
 * post buttons (core/post_actions.js), asked in Rasova's own sheet. Data and
 * addresses come from setup.views.offer_views.offers_page_data.
 */
import { api } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { withId } from "../billing/urls.js";
import { EXAMPLE_NAMES, draftRule, exampleBill } from "./offer_example.js";

const data = pageData("page-data");
const $ = (id) => document.getElementById(id);
const all = (selector) => Array.from(document.querySelectorAll(selector));
const money = (n) => "₹" + Number(n).toFixed(2);
const MINUS = "−";
const SAVE_EDIT = '<i class="bi bi-check-lg"></i> Save changes';

let template = "buy_get_free";
let editing = null;
let exampleQty = {};
let busy = false;

const picked = (cls) => all("." + cls + ":checked").map((e) => Number(e.value));
const days = () => all('.day[aria-pressed="true"]').map((e) => Number(e.dataset.day));

function setTemplate(tpl) {
  template = tpl;
  all(".tpl").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tpl === tpl)));
  $("grp-bgf").hidden = tpl !== "buy_get_free";
  $("bgf-hint").hidden = tpl !== "buy_get_free";
  $("grp-pct").hidden = !(tpl === "happy_hour" || tpl === "percent_off");
  $("grp-amt").hidden = tpl !== "amount_off";
  if (tpl === "happy_hour" && !$("f-start").value && !$("f-end").value) {
    $("f-start").value = "17:00";
    $("f-end").value = "20:00";
    all(".day").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.day) <= 4)));
  }
  $("f-name").placeholder = EXAMPLE_NAMES[tpl];
  exampleQty = {};
  renderExample();
}

function node(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function row(cls, label, value) {
  const r = node("div", cls);
  r.append(node("span", "", label), node("span", "", value));
  return r;
}

/* Every name and number is set as text, never as HTML. */
function renderExample() {
  const box = $("example");
  const rule = draftRule(template, {
    name: $("f-name").value, buy: $("f-buy").value, free: $("f-free").value,
    percent: $("f-pct").value, amount: $("f-amt").value,
    dishes: picked("pick-dish"), categories: picked("pick-cat"),
  });
  const outlet = data.outlet || {};
  const bill = exampleBill({ menu: data.menu || [], rule, qty: exampleQty, dishTax: data.dishTax || {}, outlet });
  box.replaceChildren();
  if (!bill) {
    box.textContent = "No dishes to show yet.";
    return;
  }
  exampleQty = bill.quantities;
  for (const l of bill.lines) {
    const line = node("div", "ex-line");
    const qty = node("input");
    qty.type = "number";
    qty.min = "0";
    qty.max = "20";
    qty.value = String(l.qty);
    qty.dataset.dish = String(l.dish.id);
    qty.dataset.change = "offer.qty";
    qty.setAttribute("aria-label", "Quantity of " + l.dish.name);
    line.append(qty, node("span", "name", l.dish.name), node("span", "", money(l.amount)));
    box.append(line);
    if (l.offer) box.append(node("div", "ex-offer", l.offer.name + " " + MINUS + money(l.offer.discount)));
  }
  const t = bill.totals;
  box.append(row("ex-row ex-subtotal", "Subtotal", money(t.subtotal)));
  if (t.offers > 0) box.append(row("ex-row ex-offers", "Offers", MINUS + money(t.offers)));
  if (t.gst > 0) box.append(row("ex-row", "GST" + (outlet.inclusive ? " (incl.)" : ""), money(t.gst)));
  if (t.vat > 0) box.append(row("ex-row", "VAT" + (outlet.vatInclusive ? " (incl.)" : ""), money(t.vat)));
  box.append(row("ex-row total", "Guest pays", "₹" + t.roundedTotal));
  if (t.offers > 0) box.append(node("div", "ex-save", "The guest saves " + money(t.offers) + "."));
  else box.append(node("div", "sub ex-none", "No offer on this example yet" +
    (bill.groupSize ? ": a group needs " + bill.groupSize + " covered dishes" : "") + "."));
}

function say(text, ok) {
  const el = $("form-msg");
  el.hidden = false;
  el.dataset.tone = ok ? "ok" : "bad";
  el.textContent = text;
}

async function save(btn) {
  if (busy) return;
  const payload = {
    template, name: $("f-name").value.trim(),
    buy_qty: $("f-buy").value, free_qty: $("f-free").value,
    percent: $("f-pct").value, amount: $("f-amt").value,
    dish_ids: picked("pick-dish"), category_ids: picked("pick-cat"),
    days: days(), start_time: $("f-start").value || null, end_time: $("f-end").value || null,
    valid_from: $("f-from").value || null, valid_until: $("f-until").value || null,
    priority: $("f-priority").value || 0, all_outlets: Boolean($("f-all") && $("f-all").checked),
  };
  if (!payload.name) {
    say("Give the offer a name, as the bill will show it.", false);
    return;
  }
  busy = true;
  btn.disabled = true;
  try {
    const url = editing ? withId(data.urls.update, editing) : data.urls.create;
    const d = await api.post(url, payload);
    const bills = editing && d.bills ? ` ${d.bills} open bill${d.bills === 1 ? "" : "s"} re-totalled.` : "";
    const same = editing && !(d.changes || []).length ? " Nothing was different." : "";
    say(`Saved: ${d.name} (${d.summary}).${same}${bills}`, true);
    setTimeout(() => window.location.reload(), 1200);
  } catch (err) {
    if (err && err.message && err.message !== "Offline") say(err.message, false);   // also in a toast
    btn.disabled = false;
    busy = false;
  }
}

/* Loads an offer back into the form; the example follows it. */
function startEdit(id) {
  const d = (data.forms || {})[String(id)];
  if (!d) return;
  editing = id;
  $("f-start").value = "";
  $("f-end").value = "";
  all(".day").forEach((b) => b.setAttribute("aria-pressed", "false"));
  setTemplate(d.template);
  $("f-name").value = d.name;
  $("f-buy").value = d.buy_qty;
  $("f-free").value = d.free_qty;
  if (d.percent) $("f-pct").value = d.percent;
  if (d.amount) $("f-amt").value = d.amount;
  all(".pick-cat").forEach((c) => { c.checked = d.category_ids.includes(Number(c.value)); });
  all(".pick-dish").forEach((x) => {
    const inCategory = d.category_ids.includes(Number(x.dataset.cat));
    x.disabled = inCategory;
    x.checked = !inCategory && d.dish_ids.includes(Number(x.value));
  });
  all(".day").forEach((b) => b.setAttribute("aria-pressed", String(d.days.includes(Number(b.dataset.day)))));
  $("f-start").value = d.start_time;
  $("f-end").value = d.end_time;
  $("f-from").value = d.valid_from;
  $("f-until").value = d.valid_until;
  $("f-priority").value = d.priority;
  if ($("f-all")) $("f-all").checked = Boolean(d.all_outlets);
  $("form-title").textContent = "Edit offer";
  $("form-icon").className = "bi bi-pencil";
  $("create-btn").innerHTML = SAVE_EDIT;
  $("cancel-edit").hidden = false;
  $("edit-hint").hidden = false;
  $("form-msg").hidden = true;
  all("tr.editing").forEach((r) => r.classList.remove("editing"));
  const tr = $(`row-${id}`);
  if (tr) tr.classList.add("editing");
  exampleQty = {};
  renderExample();
  if ($("form-card").scrollIntoView) $("form-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

delegate(document.body, {
  "offer.template": (b) => setTemplate(b.dataset.tpl),
  "offer.day": (b) => b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true")),
  "offer.edit": (b) => startEdit(Number(b.dataset.offer)),
  "offer.save": (b) => save(b),
  "offer.cancel": () => { window.location.href = window.location.pathname; },
});

document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.classList.contains("pick-cat")) {
    all(`.pick-dish[data-cat="${t.value}"]`).forEach((d) => {
      d.disabled = t.checked;
      if (t.checked) d.checked = false;
    });
    exampleQty = {};
    renderExample();
  } else if (t.classList.contains("pick-dish")) {
    exampleQty = {};
    renderExample();
  } else if (t.dataset.change === "offer.qty") {
    exampleQty = { ...exampleQty, [t.dataset.dish]: Math.max(0, Math.min(20, parseInt(t.value, 10) || 0)) };
    renderExample();
  }
});
["f-name", "f-buy", "f-free", "f-pct", "f-amt"].forEach((id) => $(id).addEventListener("input", () => {
  if (id === "f-buy" || id === "f-free") exampleQty = {};
  renderExample();
}));

renderExample();
