/*
 * The control panel (accounts/superuser_panel.html): New Restaurant
 * (create_restaurant.js) and the preset buttons on each restaurant's row
 * (preset.js, asked in Rasova's own sheet).
 */
import { ui } from "../core/app.js";
import { csrfToken } from "../core/csrf.js";
import { delegate } from "../core/dom.js";
import { createRestaurant } from "./create_restaurant.js";
import { applyPreset } from "./preset.js";

let busy = false;
const fetchFn = (...args) => fetch(...args);

delegate(document.body, {
  "preset.apply": async (button) => {
    if (busy) return;
    busy = true;
    try {
      await applyPreset(button, { ui, fetchFn, csrf: () => csrfToken(), reload: () => setTimeout(() => window.location.reload(), 800) });
    } finally {
      busy = false;
    }
  },
});

delegate(document.body, {
  "restaurant.create": (form, event) => {
    event.preventDefault();
    createRestaurant(form, {
      fetchFn,
      csrf: () => csrfToken(),
      status: document.getElementById("createStatus"),
      go: (url) => { window.location.href = url; },
    });
  },
}, "submit");
