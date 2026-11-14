/*
 * The weekly schedule (shifts/schedule_builder.html): tapping an empty
 * cell opens the Assign panel for that person and day; "Custom times"
 * shows the start and end fields. Assigning is a shared post form and
 * removing a shift a post button (core/post_actions.js), so the server's
 * own check ("a template, or both times") is what the person sees.
 */
import { delegate } from "../core/dom.js";

export function dayLabel(date, locale = "en-IN") {
  const d = new Date(date + "T00:00:00");
  return Number.isNaN(d.getTime()) ? date : d.toLocaleDateString(locale, { weekday: "short", day: "numeric", month: "short" });
}

export function openAssign(doc, { staff, name, date }) {
  doc.getElementById("panelPrompt").hidden = true;
  doc.getElementById("assignForm").hidden = false;
  doc.getElementById("f_staff_id").value = staff;
  doc.getElementById("f_date").value = date;
  doc.getElementById("f_staff_name").textContent = name;
  doc.getElementById("f_date_display").textContent = dayLabel(date);
}

export function showCustomTimes(doc, templateId) {
  doc.getElementById("customTimes").hidden = Boolean(templateId);
}

if (typeof document !== "undefined") {
  delegate(document.body, {
    "schedule.open": (cell) => openAssign(document, cell.dataset),
  });
  delegate(document.body, {
    "schedule.template": (select) => showCustomTimes(document, select.value),
  }, "change");
}
