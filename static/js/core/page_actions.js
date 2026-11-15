/*
 * The few buttons any page may have that need no page code: print the
 * page, reload it, go back, select a read-only field's text (to copy it).
 *
 *   <button type="button" data-action="page.print">Print</button>
 *   <input readonly data-action="field.select" value="...">
 *   <select name="date" data-action="form.submit">   (on change: a filter)
 *   <select name="date_filter" data-action="range.pick" data-range="customRange">
 *       a report's period: "custom" shows the From/To fields (the element
 *       data-range names), anything else sends the form at once
 *   <input name="code" data-action="field.upper">   (as typed: capital letters)
 *   <input type="checkbox" data-action="field.reveal" data-target="secretBox">
 *       shows the element data-target names while the box is ticked
 *   <form method="post" data-action="confirm.submit" data-confirm="Delete this client?"
 *         data-confirm-text="..." data-confirm-button="Delete">
 *       an ordinary form that asks first in Rasova's own sheet (never the
 *       browser's confirm(), which the Android app never shows)
 *
 * core/app.js handles them on every base.html page; a page without
 * base.html loads core/standalone.js, which handles them alone. A page's
 * own module never handles these names again: two listeners on one
 * button would print twice.
 */
export function pageActions(win) {
  return {
    "page.print": () => win.print(),
    "page.reload": () => win.location.reload(),
    // A page opened in its own tab (a printout) closes; otherwise back.
    "page.back": () => {
      if (win.opener) win.close();
      else win.history.back();
    },
    "field.select": (el) => el.select(),
  };
}

/* What a change (not a click) does: a filter that sends its form at once. */
export function pageChangeActions() {
  return {
    "form.submit": (el) => { if (el.form) el.form.submit(); },
    "range.pick": (el) => {
      const custom = el.value === "custom";
      const range = el.ownerDocument.getElementById(el.dataset.range);
      // !important: the range's own classes say display:flex !important.
      if (range) range.style.setProperty("display", custom ? "flex" : "none", "important");
      if (!custom && el.form) el.form.submit();
    },
    "field.reveal": (el) => {
      const target = el.ownerDocument.getElementById(el.dataset.target);
      if (target) target.hidden = !el.checked;
    },
  };
}

/* What sending a form does: an ordinary form that asks first. form.submit()
   sends it without a second submit event, so it is asked once. */
export function pageSubmitActions(ui) {
  return {
    "confirm.submit": async (form, event) => {
      event.preventDefault();
      const d = form.dataset;
      const answer = await ui.confirm(d.confirm || "Are you sure?", d.confirmText || "", d.confirmButton || "Yes, do it");
      if (answer && answer.isConfirmed) form.submit();
    },
  };
}

/* What typing does: a code kept in capital letters as it is typed. */
export function pageInputActions() {
  return {
    "field.upper": (el) => { el.value = el.value.toUpperCase(); },
  };
}
