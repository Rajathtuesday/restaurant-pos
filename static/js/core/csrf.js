/*
 * The CSRF token for any request that changes something.
 *
 * Django puts it in <meta name="csrf-token"> on every page that extends
 * base.html; the cookie is the fallback for a page without the meta tag.
 * Its name is settings.CSRF_COOKIE_NAME (core.tests.test_csrf_cookie_name
 * holds the two together: the fallback read "csrftoken", a cookie Rasova
 * never sets, until 14 Nov 2026).
 */
export const CSRF_COOKIE = "csrftoken2";

export function csrfToken(doc = document) {
  const meta = doc.querySelector('meta[name="csrf-token"]');
  if (meta) return meta.getAttribute("content") || "";
  const match = (doc.cookie || "").match(new RegExp("(?:^|;\\s*)" + CSRF_COOKIE + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : "";
}
