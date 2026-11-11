/*
 * What the server told this page.
 *
 * A view passes a dict, the template renders it once with Django's
 * json_script filter ({{ rasova_page|json_script:"rasova-data" }}), and
 * this reads it back. That is the one way server data reaches a script:
 * no template tags inside JavaScript, so the scripts are static files the
 * browser caches and node can test with a fixture.
 */
export function pageData(id = "rasova-data", doc = document) {
  const el = doc.getElementById(id);
  if (!el) return {};
  try {
    return JSON.parse(el.textContent);
  } catch (notJson) {
    return {};
  }
}
