/*
 * Setup, tables (setup/templates/setup/setup_tables.html): tap a table to
 * rename it (rename_table.js). A second tap while one is open is ignored.
 */
import { api, ui } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { renameTable } from "./rename_table.js";

let busy = false;
delegate(document.body, {
  "table.rename": async (chip) => {
    if (busy) return;
    busy = true;
    try {
      await renameTable(chip, { ui, api, reload: () => window.location.reload() });
    } finally {
      busy = false;
    }
  },
});
