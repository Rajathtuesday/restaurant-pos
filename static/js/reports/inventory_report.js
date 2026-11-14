/*
 * The inventory report (reports/inventory_report.html): its six section
 * tabs and the wastage source chips. The tab shown is kept in the filter
 * form (#currentTab), so a filter change, which reloads the page, comes
 * back to it. The period, outlet and item selects and Print are shared
 * page actions (core/page_actions.js).
 */
import { delegate, qsa } from "../core/dom.js";

export const TABS = ["ledger", "usage", "wastage", "cost", "canmake", "closing"];

export function showTab(doc, name) {
  if (!TABS.includes(name)) return false;
  for (const t of TABS) {
    const panel = doc.getElementById("tab-" + t);
    if (panel) panel.hidden = t !== name;
  }
  for (const button of qsa(".section-tab", doc)) {
    const on = button.dataset.tab === name;
    button.classList.toggle("active", on);
    button.setAttribute("aria-selected", on ? "true" : "false");
  }
  doc.getElementById("currentTab").value = name;
  return true;
}

if (typeof document !== "undefined") {
  delegate(document.body, {
    "section.show": (button) => showTab(document, button.dataset.tab),
    "wastage.source": (chip) => {
      document.getElementById("wastageSource").value = chip.dataset.source;
      document.getElementById("currentTab").value = "wastage";
      document.getElementById("filterForm").submit();
    },
  });
  showTab(document, document.getElementById("currentTab").value || "ledger");
}
