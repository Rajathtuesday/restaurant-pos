/*
 * The recipe import review page: each line saves when it changes; "+ New
 * ingredient" in a line's list asks for the name and unit (recipe_lines.js)
 * and adds it to every line's list; Save Recipe checks every kept line is
 * complete before sending. Add Line and Discard are post buttons
 * (core/post_actions.js). Addresses come from the page (data-* on the
 * table and the buttons).
 */
import { api, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { withId } from "../billing/urls.js";
import { askNewIngredient, linePayload, readyToSave } from "./recipe_lines.js";

const body = document.getElementById("lineTableBody");
const isDark = () => document.documentElement.classList.contains("dark");

async function saveLine(row) {
  try {
    await api.post(withId(body.dataset.lineUrl, row.dataset.lineId), linePayload(row));
  } catch (shown) {
    /* the client showed why */
  }
}

async function newIngredient(select, row) {
  const answer = await askNewIngredient(window.Swal, isDark);
  if (!answer) {
    select.value = "";
    return;
  }
  let data;
  try {
    data = await api.post(body.dataset.createUrl, answer);
  } catch (shown) {
    select.value = "";
    return;
  }
  for (const list of document.querySelectorAll(".match-select")) {
    const opt = document.createElement("option");
    opt.value = data.id;
    opt.dataset.unit = answer.unit;
    opt.textContent = `${answer.name} (${answer.unit})`;
    list.insertBefore(opt, list.lastElementChild);
  }
  select.value = data.id;
  const unit = row.querySelector(".unit-select");
  if (unit && !unit.value) unit.value = answer.unit;
  saveLine(row);
}

body?.addEventListener("change", (event) => {
  const row = event.target.closest(".line-row");
  if (!row) return;
  const target = event.target;
  if (target.classList.contains("match-select")) {
    if (target.value === "__new__") {
      newIngredient(target, row);
      return;
    }
    const opt = target.selectedOptions[0];
    const unit = row.querySelector(".unit-select");
    if (opt && opt.dataset.unit && unit && !unit.value) unit.value = opt.dataset.unit;
  }
  saveLine(row);
});

delegate(document.body, {
  "recipe.save": async (button) => {
    const payloads = Array.from(document.querySelectorAll(".line-row")).map(linePayload);
    if (!readyToSave(payloads)) {
      ui.toast("Every line kept for the recipe needs an ingredient, a quantity and a unit.", "warning");
      return;
    }
    button.disabled = true;
    button.textContent = "Saving…";
    try {
      const data = await api.post(button.dataset.url, {});
      ui.toast(`Saved ${data.count} recipe ingredient${data.count === 1 ? "" : "s"}.`, "success");
      setTimeout(() => { window.location.href = button.dataset.go; }, 900);
    } catch (shown) {
      button.disabled = false;
      button.textContent = "Save Recipe";
    }
  },
});
