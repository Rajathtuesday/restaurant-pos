/*
 * The waiter dashboard (waiter/waiter_dashboard.html) reloads itself when
 * its calls change: every 10 seconds it asks for the page again and
 * compares the list with what is on screen. It waits while the tab is
 * hidden, and a failed check changes nothing. Signed out, it shows the
 * page's notice and stops asking (core/signed_out.js): the sign-in page it
 * used to get back has no list, so the calls on screen simply froze
 * (28 Nov 2026). Resolve and Acknowledge are post buttons
 * (core/post_actions.js).
 */
import { knownSignedOut, showSignedOut, signedOut } from "../core/signed_out.js";

const LIST = ".row.g-4, .empty-state";

/* True when the page just fetched shows a different list. */
export function listChanged(currentDoc, freshHtml, parse) {
  const fresh = parse(freshHtml).querySelector(LIST);
  const now = currentDoc.querySelector(LIST);
  return Boolean(fresh) && fresh.innerHTML !== (now ? now.innerHTML : null);
}

export function watchList({
  doc, fetchFn, url, parse, reload,
  every = (fn, ms) => setInterval(fn, ms),
  onSignedOut = () => showSignedOut(doc),
}) {
  let asking = false;
  let signedOff = false;
  async function check() {
    if (!signedOff && knownSignedOut(doc)) signedOff = true;   // another script found out first
    if (asking || signedOff || doc.hidden) return false;
    asking = true;
    try {
      const response = await fetchFn(url, { headers: { Accept: "text/html", "X-Requested-With": "XMLHttpRequest" } });
      if (signedOut(response)) {
        signedOff = true;
        onSignedOut();
        return false;
      }
      if (!response.ok) return false;
      if (listChanged(doc, await response.text(), parse)) {
        reload();
        return true;
      }
      return false;
    } catch (down) {
      return false;
    } finally {
      asking = false;
    }
  }
  every(check, 10000);
  return check;
}

if (typeof document !== "undefined") {
  watchList({
    doc: document,
    fetchFn: (...args) => fetch(...args),
    url: window.location.href,
    parse: (html) => new DOMParser().parseFromString(html, "text/html"),
    reload: () => window.location.reload(),
  });
}
