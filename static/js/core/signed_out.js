/*
 * Signed out while a screen is open.
 *
 * One sign-in covers every tab and every restaurant address in a browser,
 * so signing out in one tab signs out the kitchen screen in another, and
 * a session can simply end. The server answers a script's request from a
 * signed-out browser with 401 (core.middleware.SignedOutScriptMiddleware);
 * before that the script followed a redirect to the sign-in page and went
 * on asking, saying nothing, for hours (28 Nov 2026).
 *
 * signedOut(response) says whether an answer means that. showSignedOut()
 * shows the notice base.html renders hidden for a signed-in page, once:
 * its words and its Sign in link (back to this page) are in the template.
 * The live screens stop asking (core/live_feed.js).
 */

/* settings.LOGIN_URL (core.tests.test_signed_out checks they agree). */
export const SIGN_IN = "/login/";

export function signedOut(response) {
  if (!response) return false;
  if (response.status === 401) return true;
  // A redirect to the sign-in page that a request without the script
  // headers still gets, followed by fetch to a 200 sign-in page.
  if (!response.redirected || !response.url) return false;
  try {
    return new URL(response.url).pathname === SIGN_IN;
  } catch (notAUrl) {
    return false;
  }
}

/* Show the notice. True the first time, false when already shown or the
   page has none (a page that never needed a sign-in). */
export function showSignedOut(doc = globalThis.document) {
  const note = doc && doc.querySelector("[data-signed-out]");
  if (!note || !note.hidden) return false;
  note.hidden = false;
  return true;
}

/* True once any script on this page has learnt it is signed out (the
   notice is up): every other poller stops before asking, instead of each
   finding out on its own next tick. */
export function knownSignedOut(doc = globalThis.document) {
  const note = doc && doc.querySelector ? doc.querySelector("[data-signed-out]") : null;
  return Boolean(note) && note.hidden === false;
}
