/*
 * Menu management, the parts with no page in sight (node tests them):
 * the dish form's fields, the search, the AI imports' polling answers,
 * the branch sync's words, posting a form with a file, and the AI menu
 * import from the photo to its last answer (the Menu page and setup step 2).
 */
import { withId } from "../billing/urls.js";

/* The fields a dish form sends. `form` holds what the page has: a value is
   undefined when the page has no such field (the QSR page has no station,
   description, prep time, parcel charge or photo), and the field is then
   left out, so saving keeps what the dish had. A new dish without a prep
   time field gets 10 minutes, as the QSR page always gave. */
export function dishFields(form, { editing }) {
  const fields = [
    ["name", form.name.trim()],
    ["price", form.price.trim()],
    ["category", form.category],
    ["is_veg", form.isVeg ? "true" : "false"],
  ];
  if (form.station !== undefined) fields.push(["station", form.station]);
  if (form.description !== undefined) fields.push(["description", form.description]);
  if (form.prepTime !== undefined) fields.push(["estimated_prep_time", form.prepTime || 15]);
  else if (!editing) fields.push(["estimated_prep_time", 10]);
  if (form.parcelCharge !== undefined) fields.push(["parcel_charge", form.parcelCharge || 0]);
  return fields;
}

export function dishProblem(form) {
  if (!form.name.trim()) return "Give the dish a name.";
  if (!form.price.trim()) return "Give the dish a price.";
  if (!form.category) return "Add a category first, then the dish.";
  return "";
}

/* For each dish name, whether the search hides it. */
export function hiddenBySearch(names, term) {
  const t = (term || "").toLowerCase().trim();
  return names.map((name) => Boolean(t) && !String(name).toLowerCase().includes(t));
}

/* The menu import's poll answer: {state: "wait" | "done" | "failed", words}. */
export function menuImportStep(answer) {
  if (!answer) return { state: "wait" };
  if (answer.status === "done") return { state: "done", words: answer.message || "Menu imported." };
  if (answer.status === "error") return { state: "failed", words: "AI import failed: " + (answer.error || "unknown error") };
  if (answer.status === "not_found") return { state: "failed", words: "The import was lost. Please try again." };
  return { state: "wait" };
}

/* The recipe import's poll answer. */
export function recipeImportStep(answer) {
  if (!answer) return { state: "wait" };
  if (answer.status === "ready_for_review") return { state: "done" };
  if (answer.status === "failed") return { state: "failed", words: "Recipe import failed: " + (answer.error || "unknown error") };
  return { state: "wait" };
}

export function syncWords(stats) {
  const s = stats || {};
  return `Sync complete: ${s.categories_created || 0} categories, ${s.items_created || 0} dishes, `
    + `${s.recipes_created || 0} recipes across ${s.outlets_updated || 0} branches.`;
}

/* Posts a FormData (a photo or a file goes with it, so not JSON). Returns
   {ok, data, message}; never throws. */
export async function postForm({ fetchFn, csrf, url, form }) {
  let response;
  try {
    response = await fetchFn(url, { method: "POST", headers: { "X-CSRFToken": csrf() }, body: form });
  } catch (down) {
    return { ok: false, message: "Network failure or server is unreachable." };
  }
  let data;
  try {
    data = await response.json();
  } catch (notJson) {
    return { ok: false, message: `Server Error (${response.status}). Please try again.` };
  }
  if (!response.ok || data.error || data.success === false) {
    return { ok: false, data, message: data.error || `Server Error (${response.status})` };
  }
  return { ok: true, data };
}

/* Asks `get()` every `every` ms until `step(answer).state` is not "wait",
   or `limit` asks pass. Returns the last step. */
export async function pollUntil({ get, step, every = 2000, limit = 150, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) }) {
  for (let i = 0; i < limit; i += 1) {
    await sleep(every);
    let answer = null;
    try {
      answer = await get();
    } catch (dropped) {
      answer = null;            // asked again next turn
    }
    const next = step(answer);
    if (next.state !== "wait") return next;
  }
  return { state: "failed", words: "The import is taking too long. Please try again." };
}

/* The AI menu import, from the photo (or text) to its last answer: the one
   importer both the Menu page and setup step 2 use. The importer
   (menu.views.ai_views.ai_menu_importer) reads "file" and "text"; it queues
   the job and answers {queued, task_id}, so this asks `statusUrl` (with
   "/0/" for the task id) until the job is done. When the queue is down it
   reads the menu there and then and answers {message}. Returns
   {state: "done" | "failed", words}; never throws.

   Until 28 Nov 2026 step 2 had its own copy: it sent the photo as "image"
   and took the queued answer for a failure, so a new owner only ever read
   "Import failed. Try again." */
export async function importMenu({ fetchFn, csrf, url, statusUrl, file, text = "", onQueued = () => {},
  every = 2000, sleep }) {
  // A Rasova menu file is not a menu to read: the page sends it to Menu
  // file's check instead (sendToMenuFileCheck), before any upload.
  if (await isMenuFile(file)) return { state: "menu_file" };
  const form = new FormData();
  if (file) form.append("file", file);
  form.append("text", text);
  const sent = await postForm({ fetchFn, csrf, url, form });
  if (!sent.ok) return { state: "failed", words: sent.message };
  if (!sent.data.queued) return { state: "done", words: sent.data.message || "Menu imported." };
  onQueued();
  const get = async () => (await fetchFn(withId(statusUrl, sent.data.task_id),
    { headers: { Accept: "application/json" } })).json();
  return pollUntil({ get, step: menuImportStep, every, ...(sleep ? { sleep } : {}) });
}

/* Is `file` a Rasova menu file ("format": "rasova-menu", what Menu file's
   Save gives)? Read here, on the phone, before anything is sent. */
export async function isMenuFile(file) {
  if (!file || file.size > 2 * 1024 * 1024 || typeof file.text !== "function") return false;
  try {
    const data = JSON.parse(await file.text());
    return Boolean(data) && data.format === "rasova-menu";
  } catch (notJson) {
    return false;
  }
}

/* Sends a menu file to Menu file's check (menu.views.file_views
   .menu_file_check), as its own form would: the page then shows what
   loading it would add, one tap from done. 28 Nov 2026: the owner gave the
   file to the AI import four times, was told where it goes, and never got
   there; now it goes there itself. */
export function sendToMenuFileCheck({ doc = document, url, csrf, file }) {
  const form = doc.createElement("form");
  form.method = "post";
  form.action = url;
  form.enctype = "multipart/form-data";
  form.hidden = true;
  const token = doc.createElement("input");
  token.type = "hidden";
  token.name = "csrfmiddlewaretoken";
  token.value = csrf();
  const input = doc.createElement("input");
  input.type = "file";
  input.name = "menu_file";
  const files = new DataTransfer();
  files.items.add(file);
  input.files = files.files;
  form.append(token, input);
  doc.body.append(form);
  form.submit();
}
