/*
 * The control panel's page for one restaurant: the preset buttons
 * (preset.js). A second tap while one is out is ignored.
 */
import { ui } from "../core/app.js";
import { csrfToken } from "../core/csrf.js";
import { delegate } from "../core/dom.js";
import { applyPreset } from "./preset.js";

let busy = false;
delegate(document.body, {
  "preset.apply": async (button) => {
    if (busy) return;
    busy = true;
    try {
      await applyPreset(button, {
        ui,
        fetchFn: (...args) => fetch(...args),
        csrf: () => csrfToken(),
        reload: () => setTimeout(() => window.location.reload(), 800),
      });
    } finally {
      busy = false;
    }
  },
});
