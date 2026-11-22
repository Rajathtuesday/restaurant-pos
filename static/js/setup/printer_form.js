/*
 * One printer's row on Setup > Printers, as plain data (printers.js posts
 * it to setup.views.printer_views.printer_save). Only the fields the row
 * really has are sent: a box's row has no USB printer name, and a field
 * that isn't there is left as it was on the server, never cleared.
 */
export const PRINTER_FIELDS = [
  "printer_ip", "printer_port", "paper_width_mm", "cut_type", "printer_encoding", "printer_mac", "printer_name",
];

export function readPrinterForm(form) {
  const out = {};
  for (const name of PRINTER_FIELDS) {
    const field = form.elements[name];
    if (field) out[name] = String(field.value || "").trim();
  }
  return out;
}

const LEVELS = new Set(["ok", "bad", "idle", "none"]);

/* The status line's class for a level from the server; anything unknown is quiet. */
export function stateClass(level) {
  return "is-" + (LEVELS.has(level) ? level : "none");
}
