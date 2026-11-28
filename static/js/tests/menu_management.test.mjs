/* Menu management: the parts with no page (static/js/menu/manage_parts.js). */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  dishFields, dishProblem, hiddenBySearch, importMenu, menuImportStep, pollUntil, postForm, recipeImportStep, syncWords,
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

/* A fetch that answers in turn, and notes what it was asked. */
function server(...answers) {
  const asked = [];
  const fetchFn = async (url, options = {}) => {
    asked.push({ url, options });
    const [status, body] = answers.shift();
    return { ok: status < 400, status, json: async () => body };
  };
  return { fetchFn, asked };
}
const quick = { every: 0, sleep: async () => {} };
const photo = new Blob(["png"], { type: "image/png" });

test("the AI import sends the photo as file, then asks for the queued job until it is done", async () => {
  const { fetchFn, asked } = server(
    [200, { queued: true, task_id: "t-9" }],
    [200, { status: "processing" }],
    [200, { status: "done", message: "AI imported 3 items." }],
  );
  let queued = false;
  const end = await importMenu({ fetchFn, csrf: () => "tok", url: "/menu/ai-import/", statusUrl: "/menu/ai-import-status/0/",
    file: photo, onQueued: () => { queued = true; }, ...quick });
  assert.deepEqual(end, { state: "done", words: "AI imported 3 items." });
  assert.ok(queued);
  // the importer reads "file" (setup step 2 sent "image" until 28 Nov 2026)
  assert.equal(asked[0].options.body.get("file").type, "image/png");
  assert.equal(asked[0].options.body.get("image"), null);
  assert.equal(asked[0].options.headers["X-CSRFToken"], "tok");
  assert.deepEqual(asked.slice(1).map((a) => a.url), ["/menu/ai-import-status/t-9/", "/menu/ai-import-status/t-9/"]);
});

test("the AI import: read at once when the queue is down, and a failed job says why", async () => {
  const at_once = server([200, { success: true, message: "Imported 12 dishes." }]);
  assert.deepEqual(await importMenu({ fetchFn: at_once.fetchFn, csrf: () => "", url: "/i/", statusUrl: "/s/0/",
    file: photo, ...quick }), { state: "done", words: "Imported 12 dishes." });

  const failed = server([200, { queued: true, task_id: "t-1" }], [200, { status: "error", error: "Could not read the photo." }]);
  assert.deepEqual(await importMenu({ fetchFn: failed.fetchFn, csrf: () => "", url: "/i/", statusUrl: "/s/0/",
    file: photo, ...quick }), { state: "failed", words: "AI import failed: Could not read the photo." });

  const refused = server([400, { error: "Image too large: please use a photo under 15 MB." }]);
  assert.deepEqual(await importMenu({ fetchFn: refused.fetchFn, csrf: () => "", url: "/i/", statusUrl: "/s/0/",
    file: photo, ...quick }), { state: "failed", words: "Image too large: please use a photo under 15 MB." });
});
