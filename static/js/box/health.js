/*
 * Box health (box/templates/box/health.html): follows a pen drive update
 * while it runs (update_watch.js).
 */
import { createUpdateWatch } from "./update_watch.js";

const panel = document.getElementById("updatePanel");
if (panel && panel.dataset.working) {
  const message = document.getElementById("updateMessage");
  createUpdateWatch({
    fetchFn: (...args) => fetch(...args),
    url: panel.dataset.statusUrl,
    startState: panel.dataset.state,
    reload: () => window.location.reload(),
    show: (words) => { if (message) message.textContent = words; },
  }).run();
}
