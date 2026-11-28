/*
 * Menu management, both pages (menu/menu_management.html and the cafe and
 * franchise one, menu/qsr_menu_management.html): categories, the dish form,
 * availability, kitchen station, recipe lines, the AI menu and recipe
 * imports, the branch sync, the search and (full page) the phone drawer.
 *
 * Delete dish, Delete category and the platform switches are post buttons
 * (core/post_actions.js): a refusal (a dish on past bills) is a toast, where
 * the page used to reload and say nothing. Every question is asked in
 * Rasova's own sheet and every answer is a toast: confirm() and alert()
 * never show in the Android app. Data and addresses come from
 * menu.views.management_views.menu_page_data.
 */
import { api, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { withId } from "../billing/urls.js";
import { goTo } from "../core/site_path.js";
import {
  dishFields, dishProblem, hiddenBySearch, importMenu, pollUntil, postForm, recipeImportStep, sendToMenuFileCheck,
  syncWords,
} from "./manage_parts.js";

const data = pageData("page-data");
const $ = (id) => document.getElementById(id);
const all = (selector) => Array.from(document.querySelectorAll(selector));
const modal = (id) => window.bootstrap.Modal.getOrCreateInstance($(id));
const FULL = data.kind === "full";
const reload = () => window.location.reload();
const getJson = async (url) => {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  return response.json();
};

let recipeDish = null;
let importDish = null;
let busy = false;

/* ---- the dish form ------------------------------------------------------- */

const value = (id) => ($(id) ? $(id).value : undefined);

function readDishForm() {
  const veg = $("itemIsVeg");
  return {
    name: $("itemName").value,
    price: $("itemPrice").value,
    category: $("itemCategory").value,
    isVeg: veg.type === "radio" ? veg.checked : veg.value === "true",
    station: value("itemStation"),
    description: value("itemDescription"),
    prepTime: value("itemPrepTime"),
    parcelCharge: value("itemParcelCharge"),
  };
}

function setVeg(isVeg) {
  const veg = $("itemIsVeg");
  if (veg.type === "radio") $(isVeg ? "itemIsVeg" : "itemIsNonVeg").checked = true;
  else veg.value = isVeg ? "true" : "false";
}

function openDish(id) {
  const dish = id ? (data.dishes || {})[String(id)] : null;
  $("editItemId").value = dish ? String(dish.id) : "";
  $("itemName").value = dish ? dish.name : "";
  $("itemPrice").value = dish ? dish.price : "";
  if (dish) $("itemCategory").value = String(dish.category);
  setVeg(dish ? dish.isVeg : true);
  if ($("itemDescription")) $("itemDescription").value = dish ? dish.description : "";
  if ($("itemStation")) $("itemStation").value = dish ? String(dish.station) : "";
  if ($("itemPrepTime")) $("itemPrepTime").value = dish ? String(dish.prepTime || 15) : "15";
  if ($("itemParcelCharge")) $("itemParcelCharge").value = dish ? String(parseFloat(dish.parcelCharge || 0)) : "0";
  if ($("itemImage")) $("itemImage").value = "";
  $("itemModalTitle").textContent = dish ? (FULL ? "Edit Menu Selection" : "Edit Item") : (FULL ? "New Menu Selection" : "New Item");
  $("saveItemBtn").textContent = dish ? (FULL ? "Refine Selection" : "Save Item") : (FULL ? "Add to Collection" : "Save Item");
  modal("itemModal").show();
}

async function saveDish(btn) {
  if (busy) return;
  const form = readDishForm();
  const problem = dishProblem(form);
  if (problem) {
    ui.toast(problem, "error");
    return;
  }
  const id = $("editItemId").value;
  const body = new FormData();
  for (const [key, v] of dishFields(form, { editing: Boolean(id) })) body.append(key, v);
  const image = $("itemImage");
  if (image && image.files && image.files[0]) body.append("image", image.files[0]);
  busy = true;
  btn.disabled = true;
  const sent = await postForm({ fetchFn: fetch, csrf: api.getCsrf, url: id ? withId(data.urls.updateItem, id) : data.urls.createItem, form: body });
  if (sent.ok) {
    reload();
    return;
  }
  ui.toast(sent.message || "Could not save the dish.", "error");
  btn.disabled = false;
  busy = false;
}

/* ---- categories, availability, station, recipe ---------------------------- */

async function post(url, body, done) {
  if (busy) return false;
  busy = true;
  try {
    const answer = await api.post(url, body);
    if (done) done(answer);
    return true;
  } catch (shown) {
    return false;   // the client showed why
  } finally {
    busy = false;
  }
}

function createCategory() {
  const name = $("categoryName").value.trim();
  if (!name) {
    ui.toast("Give the category a name.", "error");
    return;
  }
  post(data.urls.createCategory, { name }, reload);
}

async function toggleSwitch(box) {
  const id = box.dataset.item;
  const on = box.checked;
  const ok = await post(withId(data.urls.toggleItem, id), {}, () => {
    const badge = $(`badge-${id}`);
    if (badge) badge.hidden = on;
    ui.toast(on ? "Item set to Available" : "Item set to Offline", on ? "success" : "error");
  });
  if (!ok) box.checked = !on;
}

async function changeStation(select) {
  const before = select.dataset.was ?? "";
  const station = select.value;
  const ok = await post(withId(data.urls.updateStation, select.dataset.item), { station }, (d) => {
    ui.toast(station ? "KOT routed to " + (d.station_name || "station") : "Routed to default station", "success");
    select.dataset.was = station;
  });
  if (!ok) select.value = before;
}

function openRecipe(id) {
  recipeDish = id;
  $("recipeQty").value = "";
  modal("recipeModal").show();
}

function addRecipeLine() {
  const ingredient = $("inventorySelect").value;
  const quantity = $("recipeQty").value.trim();
  if (!ingredient || !quantity) {
    ui.toast("Please select an ingredient and enter a quantity.", "error");
    return;
  }
  post(data.urls.addRecipe, { menu_item: recipeDish, inventory_item: ingredient, quantity }, () => {
    ui.toast("Ingredient added to recipe.", "success");
    setTimeout(reload, 600);
  });
}

function toggleRecipeBlock(id) {
  const block = $(`recipe-block-${id}`);
  block.hidden = !block.hidden;
}

/* ---- the imports and the sync ---------------------------------------------- */

function busyButton(btn, words) {
  btn.disabled = true;
  btn.replaceChildren();
  const spin = document.createElement("span");
  spin.className = "spinner-border spinner-border-sm me-2";
  btn.append(spin, document.createTextNode(words));
}

function freeButton(btn, words) {
  btn.disabled = false;
  btn.textContent = words;
}

async function runMenuImport(btn) {
  if (btn.disabled) return;
  const text = $("aiMenuText").value;
  const file = $("aiMenuFile").files[0];
  const label = "Process with Smart AI";
  if (!text.trim() && !file) {
    ui.toast("Please provide text or an image.", "error");
    return;
  }
  busyButton(btn, "Uploading…");
  // The one importer, shared with setup step 2 (manage_parts.js)
  const end = await importMenu({
    fetchFn: (...args) => fetch(...args), csrf: api.getCsrf, url: data.urls.aiImport,
    statusUrl: data.urls.aiStatus, file, text, onQueued: () => busyButton(btn, "AI is processing…"),
  });
  if (end.state === "menu_file") {
    // A Rasova menu file: Menu file's check shows what it would add
    sendToMenuFileCheck({ url: data.urls.menuFileCheck, csrf: api.getCsrf, file });
    return;
  }
  if (end.state === "done") {
    ui.toast(end.words, "success");
    setTimeout(reload, 1500);
  } else {
    ui.toast(end.words, "error");
    freeButton(btn, label);
  }
}

function openRecipeImport(button) {
  importDish = button.dataset.item;
  $("recipeImportItemName").textContent = button.dataset.name;
  $("recipeImportFile").value = "";
  $("recipeImportText").value = "";
  modal("recipeImportModal").show();
}

async function runRecipeImport(btn) {
  if (btn.disabled) return;
  const text = $("recipeImportText").value;
  const file = $("recipeImportFile").files[0];
  const label = "Extract Ingredients";
  if (!text.trim() && !file) {
    ui.toast("Please provide text or upload a file.", "error");
    return;
  }
  busyButton(btn, "Uploading…");
  const form = new FormData();
  form.append("menu_item", importDish);
  if (file) form.append("file", file);
  form.append("text", text);
  const sent = await postForm({ fetchFn: fetch, csrf: api.getCsrf, url: data.urls.recipeStart, form });
  if (!sent.ok) {
    ui.toast(sent.message, "error");
    freeButton(btn, label);
    return;
  }
  busyButton(btn, "AI is reading the recipe…");
  const jobId = sent.data.job_id;
  const end = await pollUntil({ get: () => getJson(withId(data.urls.recipeStatus, jobId)), step: recipeImportStep });
  if (end.state === "done") {
    goTo(withId(data.urls.recipeReview, jobId));
  } else {
    ui.toast(end.words, "error");
    freeButton(btn, label);
  }
}

async function syncMenu() {
  const answer = await ui.confirm("Push this menu to all branches?",
    "Categories, dishes, prices and recipe ingredients are copied to every branch at once.", "Sync");
  if (!answer || !answer.isConfirmed) return;
  ui.toast("Syncing across branches...", "info");
  post(data.urls.sync, {}, (d) => ui.toast(syncWords(d.stats), "success"));
}

/* ---- search, phone drawer, sticky nav ---------------------------------------- */

function search(term) {
  const rows = all(".menu-item-row");
  const hide = hiddenBySearch(rows.map((r) => r.querySelector(".item-name").textContent), term);
  rows.forEach((row, i) => row.classList.toggle("search-hidden", hide[i]));
  all(".category-card").forEach((card) => {
    const shown = Array.from(card.querySelectorAll(".menu-item-row")).some((r) => !r.classList.contains("search-hidden"));
    card.parentElement.classList.toggle("search-hidden", Boolean(term.trim()) && !shown);
  });
}

function mobileNav(open) {
  const drawer = $("mobileNavDrawer");
  if (!drawer) return;
  drawer.classList.toggle("open", open);
  document.body.style.overflow = open ? "hidden" : "";
}

function fixStickyNav() {
  const header = document.querySelector(".pos-header");
  const nav = document.querySelector(".cat-nav-bar");
  if (!header || !nav) return;
  const headerHeight = header.getBoundingClientRect().height;
  const navHeight = nav.getBoundingClientRect().height;
  nav.style.top = headerHeight + "px";
  all(".category-section").forEach((s) => { s.style.scrollMarginTop = (headerHeight + navHeight + 16) + "px"; });
}

delegate(document.body, {
  "menu.newCategory": () => modal("categoryModal").show(),
  "menu.saveCategory": () => createCategory(),
  "menu.newDish": () => openDish(null),
  "menu.editDish": (el) => openDish(el.dataset.item),
  "menu.saveDish": (b) => saveDish(b),
  "menu.recipe": (b) => openRecipe(b.dataset.item),
  "menu.recipeBlock": (b) => toggleRecipeBlock(b.dataset.item),
  "menu.saveRecipe": () => addRecipeLine(),
  "menu.aiImport": () => { mobileNav(false); modal("aiImportModal").show(); },
  "menu.runAiImport": (b) => runMenuImport(b),
  "menu.recipeImport": (b) => openRecipeImport(b),
  "menu.runRecipeImport": (b) => runRecipeImport(b),
  "menu.sync": () => syncMenu(),
  "menu.openNav": () => mobileNav(true),
  "menu.closeNav": () => mobileNav(false),
});
delegate(document.body, {
  "menu.available": (box) => toggleSwitch(box),
  "menu.station": (select) => changeStation(select),
}, "change");

all("[data-action='menu.station']").forEach((s) => { s.dataset.was = s.value; });
const searchBox = $("menuManagementSearch");
if (searchBox) searchBox.addEventListener("input", () => search(searchBox.value));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") mobileNav(false); });
if (FULL) {
  fixStickyNav();
  window.addEventListener("resize", fixStickyNav);
}
