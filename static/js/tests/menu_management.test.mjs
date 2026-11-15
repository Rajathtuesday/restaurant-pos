/* Menu management: the parts with no page (static/js/menu/manage_parts.js). */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  dishFields, dishProblem, hiddenBySearch, menuImportStep, pollUntil, postForm, recipeImportStep, syncWords,
} from "../menu/manage_parts.js";

const qsrForm = { name: " Burger ", price: "120", category: "3", isVeg: false };
const fullForm = { ...qsrForm, station: "", description: "Juicy", prepTime: "", parcelCharge: "5" };

test("the QSR form leaves out what it has no field for, so an edit keeps it", () => {
  assert.deepEqual(dishFields(qsrForm, { editing: true }),
    [["name", "Burger"], ["price", "120"], ["category", "3"], ["is_veg", "false"]]);
  assert.deepEqual(dishFields(qsrForm, { editing: false }).at(-1), ["estimated_prep_time", 10]);
});

test("the full form sends every field; an empty station means the default one", () => {
  const fields = Object.fromEntries(dishFields(fullForm, { editing: true }));
  assert.deepEqual(fields, { name: "Burger", price: "120", category: "3", is_veg: "false", station: "",
    description: "Juicy", estimated_prep_time: 15, parcel_charge: "5" });
});

test("a dish needs a name, a price and a category", () => {
  assert.match(dishProblem({ ...qsrForm, name: " " }), /name/);
  assert.match(dishProblem({ ...qsrForm, price: "" }), /price/);
  assert.match(dishProblem({ ...qsrForm, category: "" }), /category/);
  assert.equal(dishProblem(qsrForm), "");
});

test("search: case-blind, blank shows all", () => {
  assert.deepEqual(hiddenBySearch(["Paneer Tikka", "Dal"], "PANEER"), [false, true]);
  assert.deepEqual(hiddenBySearch(["Paneer Tikka", "Dal"], "  "), [false, false]);
});

test("the imports' poll answers", () => {
  assert.equal(menuImportStep({ status: "processing" }).state, "wait");
  assert.equal(menuImportStep(null).state, "wait");
  assert.deepEqual(menuImportStep({ status: "done", message: "12 dishes" }), { state: "done", words: "12 dishes" });
  assert.match(menuImportStep({ status: "error", error: "blurry" }).words, /blurry/);
  assert.equal(menuImportStep({ status: "not_found" }).state, "failed");
  assert.equal(recipeImportStep({ status: "ready_for_review" }).state, "done");
  assert.match(recipeImportStep({ status: "failed" }).words, /unknown error/);
});

test("polling asks again after a dropped answer and gives up in the end", async () => {
  const answers = [Promise.reject(new Error("down")), Promise.resolve({ status: "processing" }), Promise.resolve({ status: "ready_for_review" })];
  let asked = 0;
  const end = await pollUntil({ get: () => answers[asked++], step: recipeImportStep, sleep: async () => {} });
  assert.equal(end.state, "done");
  assert.equal(asked, 3);
  const never = await pollUntil({ get: async () => ({ status: "processing" }), step: recipeImportStep, sleep: async () => {}, limit: 3 });
  assert.equal(never.state, "failed");
});

test("posting a form: refusal, HTML page, dropped connection, success", async () => {
  const send = (fetchFn) => postForm({ fetchFn, csrf: () => "t", url: "/x/", form: {} });
  const json = (status, body) => async () => ({ ok: status < 400, status, json: async () => body });
  assert.deepEqual(await send(json(400, { error: "Missing fields" })), { ok: false, data: { error: "Missing fields" }, message: "Missing fields" });
  assert.match((await send(async () => ({ ok: false, status: 502, json: async () => { throw new Error("html"); } }))).message, /502/);
  assert.match((await send(async () => { throw new TypeError("down"); })).message, /unreachable/);
  assert.deepEqual(await send(json(200, { success: true })), { ok: true, data: { success: true } });
});

test("the sync's words", () => {
  assert.equal(syncWords({ categories_created: 2, items_created: 9, recipes_created: 4, outlets_updated: 3 }),
    "Sync complete: 2 categories, 9 dishes, 4 recipes across 3 branches.");
});
