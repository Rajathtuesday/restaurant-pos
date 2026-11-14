/*
 * The cash register page: Start Session and Finalize (session.js). A
 * second tap while the first is out is ignored, so a register is never
 * opened or closed twice by a double tap.
 */
import { api, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { submitSession } from "./session.js";

let busy = false;
delegate(document.body, {
  "session.submit": async (button) => {
    if (busy) return;
    busy = true;
    try {
      await submitSession(button, { api, ui, doc: document, reload: () => window.location.reload() });
    } finally {
      busy = false;
    }
  },
});
