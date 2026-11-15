/*
 * Menu management, the parts with no page in sight (node tests them):
 * the dish form's fields, the search, the AI imports' polling answers,
 * the branch sync's words, and posting a form with a file.
 */

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
