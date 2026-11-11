/*
 * The CSRF token for any request that changes something.
 *
 * Django puts it in <meta name="csrf-token"> on every page that extends
 * base.html; the csrftoken cookie is the fallback for a page without the
 * meta tag (a public page that still posts, like guest feedback).
 */
export function csrfToken(doc = document) {
  const meta = doc.querySelector('meta[name="csrf-token"]');
  if (meta) return meta.getAttribute("content") || "";
  const match = (doc.cookie || "").match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}
