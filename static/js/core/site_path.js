/*
 * Addresses a script navigates to, or puts in a link or a button, when they
 * come from the page (its data block, an element): only paths on this site.
 *
 * The server writes them (Django's reverse()), so they are "/bill/7/" and the
 * like. Read back out of the page they are, to a browser, just text, and a
 * "javascript:" address there would run as script. CodeQL flagged seven
 * places that used such text as an address (js/xss-through-dom, 21 Nov 2026).
 * Every one now goes through here: "/..." passes; "//other.host",
 * "javascript:...", "https://..." and anything else does not.
 */
const SITE_PATH = /^\/(?![/\\])/;

/* `value` if it is a path on this site, otherwise null. */
export function sitePath(value) {
  const text = String(value ?? "");
  return SITE_PATH.test(text) ? text : null;
}

/* Go to a path on this site. Anything else is refused (false). */
export function goTo(value, win = window) {
  const path = sitePath(value);
  if (path === null) return false;
  win.location.href = path;
  return true;
}
