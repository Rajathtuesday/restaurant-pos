/*
 * The few buttons any page may have that need no page code: print the
 * page, reload it, go back, select a read-only field's text (to copy it).
 *
 *   <button type="button" data-action="page.print">Print</button>
 *   <input readonly data-action="field.select" value="...">
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
