/*
 * The control panel's New Restaurant form (accounts/superuser_panel.html):
 * sent as a normal form post (data-url), its progress in the status line,
 * then on to the new restaurant's page (data-tenant-url, 0 for its id).
 */
import { withId } from "../billing/urls.js";

export async function createRestaurant(form, { fetchFn, csrf, status, go, wait = (fn, ms) => setTimeout(fn, ms) }) {
  const button = form.querySelector("button[type=submit]");
  const say = (text, tone) => {
    status.textContent = text;
    status.dataset.tone = tone;
  };
  if (button) button.disabled = true;
  say("Creating…", "wait");
  let d = {};
  try {
    const response = await fetchFn(form.dataset.url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": csrf() },
      body: new URLSearchParams(new FormData(form)).toString(),
    });
    d = await response.json().catch(() => ({ error: `Server error (${response.status}).` }));
  } catch (down) {
    d = { error: "Check the connection." };
  }
  if (d.success) {
    say("Created! Opening it…", "ok");
    wait(() => go(withId(form.dataset.tenantUrl, d.tenant_id)), 800);
    return true;
  }
  say(d.error || "Failed", "bad");
  if (button) button.disabled = false;
  return false;
}
