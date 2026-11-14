/*
 * The setup wizard (setup/templates/setup/onboard.html): the GST pricing
 * switch, the web address (edit, then checked as typed, half a second
 * after the last key), the logo preview, the AI menu import, the sample
 * menu card and the "Will create: T1, T2, ..." line for tables. Each piece
 * acts only if its step is on the page. A card that is a button answers
 * Enter and Space as well as a tap.
 */
import { delegate } from "../core/dom.js";

/* The address as it may be: lower case letters, digits and dashes. */
export function cleanSlug(typed) {
  return String(typed || "").toLowerCase().replace(/[^a-z0-9-]/g, "");
}

export function slugMessage(answer) {
  return answer.available
    ? { text: `${answer.slug}.rasova.net is available`, tone: "ok" }
    : { text: answer.reason || "Not available", tone: "err" };
}

/* "Will create: T1, T2, T3, T4, T5 … T8" for the table step. */
export function tablePreview(count, prefix) {
  const n = Number.parseInt(count, 10) || 0;
  const p = prefix || "T";
  if (n <= 0) return "";
  const names = Array.from({ length: Math.min(n, 5) }, (_, i) => p + (i + 1));
  return "Will create: " + names.join(", ") + (n > 5 ? " … " + p + n : "");
}

function el(id) {
  return document.getElementById(id);
}

function syncGstSeg() {
  for (const opt of document.querySelectorAll(".gst-seg-opt")) {
    const input = opt.querySelector("input");
    opt.classList.toggle("active", Boolean(input && input.checked));
  }
}

let slugTimer = null;
function checkSlug(input) {
  const slug = cleanSlug(input.value);
  input.value = slug;
  el("subdomainDisplay").textContent = slug || input.placeholder;
  const status = el("slugStatus");
  clearTimeout(slugTimer);
  const say = (text, tone) => { status.textContent = text; status.className = "slug-status slug-" + tone; };
  if (slug.length < 3) {
    say(slug ? "Too short: minimum 3 characters" : "", "err");
    return;
  }
  say("Checking…", "chk");
  slugTimer = setTimeout(async () => {
    try {
      const answer = await (await fetch(input.dataset.checkUrl + "?slug=" + encodeURIComponent(slug))).json();
      const { text, tone } = slugMessage(answer);
      say(text, tone);
    } catch (down) {
      say("", "chk");
    }
  }, 450);
}

async function aiImport(input) {
  if (!input.files || !input.files[0]) return;
  const spinner = el("ai-spinner");
  const done = el("ai-success");
  const failed = el("ai-failed");
  el("ai-status").hidden = false;
  spinner.hidden = false;
  done.hidden = true;
  failed.hidden = true;
  const form = new FormData();
  form.append("image", input.files[0]);
  form.append("csrfmiddlewaretoken", document.querySelector("[name=csrfmiddlewaretoken]").value);
  let data;
  try {
    data = await (await fetch(input.dataset.url, { method: "POST", body: form })).json();
  } catch (down) {
    data = { success: false, message: "Network error. Please try again." };
  }
  spinner.hidden = true;
  if (data.success) {
    el("ai-success-msg").textContent = data.message;
    done.hidden = false;
  } else {
    failed.textContent = data.message || "Import failed. Try again.";
    failed.hidden = false;
  }
}

const cardActions = {
  "file.pick": (card) => el(card.dataset.target).click(),
  "onboard.sample": (card) => card.closest("form").submit(),
};

if (typeof document !== "undefined") {
  delegate(document.body, {
    ...cardActions,
    "slug.edit": (button) => {
      const edit = el("subdomainEdit");
      edit.hidden = !edit.hidden;
      button.setAttribute("aria-expanded", edit.hidden ? "false" : "true");
      if (!edit.hidden) el("slugInput").focus();
    },
  });
  document.body.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const card = event.target.closest('[role="button"][data-action]');
    const act = card && cardActions[card.dataset.action];
    if (!act) return;
    event.preventDefault();
    act(card);
  });
  delegate(document.body, {
    "gst.seg": () => syncGstSeg(),
    "logo.preview": (input) => {
      if (!input.files || !input.files[0]) return;
      const reader = new FileReader();
      reader.onload = (e) => { const img = el("logoPreview"); img.src = e.target.result; img.classList.add("show"); };
      reader.readAsDataURL(input.files[0]);
    },
    "ai.import": (input) => aiImport(input),
  }, "change");
  delegate(document.body, {
    "slug.check": (input) => checkSlug(input),
    "tables.preview": () => { el("table-preview").textContent = tablePreview(el("tbl-count").value, el("tbl-prefix").value); },
  }, "input");
  syncGstSeg();
  if (el("table-preview")) el("table-preview").textContent = tablePreview(el("tbl-count").value, el("tbl-prefix").value);
}
