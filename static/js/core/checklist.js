/*
 * The setup checklist in the corner of every page until the restaurant has
 * finished setting up (then it hides itself for good on this device).
 */
const HIDE_KEY = "rasova_setup_done";

export function setupChecklist({
  fetchFn = (...args) => globalThis.fetch(...args),
  doc = document,
  storage = globalThis.localStorage,
} = {}) {
  const widget = doc.getElementById("setup-checklist-widget");
  if (!widget) return null;
  if (storage.getItem(HIDE_KEY) === "1") return null;

  const panel = doc.getElementById("setup-checklist-panel");
  const label = doc.getElementById("setup-checklist-label");
  const items = doc.getElementById("setup-checklist-items");
  let open = false;

  function toggle() {
    open = !open;
    panel.hidden = !open;
  }

  function render(steps, done, total) {
    label.textContent = "Setup " + done + "/" + total + " complete";
    items.innerHTML = steps.map((s) => (
      '<a href="' + s.url + '" class="setup-checklist__item' + (s.done ? " is-done" : "") + '">'
      + '<span class="setup-checklist__mark">' + (s.done ? "&#10003;" : "&#9711;") + "</span>"
      + "<span>" + s.label + "</span></a>"
    )).join("");
  }

  widget.addEventListener("click", (e) => {
    if (e.target.closest('[data-action="checklist.toggle"]')) toggle();
  });

  fetchFn("/setup/checklist/", { headers: { "X-Requested-With": "XMLHttpRequest" } })
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (!data) return;
      if (data.all_done) {
        storage.setItem(HIDE_KEY, "1");
        return;
      }
      render(data.steps, data.done_count, data.steps.length);
      widget.hidden = false;
    })
    .catch(() => {});

  return { toggle };
}
