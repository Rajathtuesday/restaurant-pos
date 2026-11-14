/*
 * Applying a feature preset to a restaurant (accounts/superuser_tenant.html).
 * The button carries its address (data-url), the preset (data-preset), its
 * label and the restaurant's name. The question is asked in Rasova's own
 * sheet, with the names as text: confirm() never shows in the Android app,
 * and the page used to put the restaurant's name straight into its script.
 * Resolves to true when the preset was applied (the page then reloads).
 */
export async function applyPreset(button, { ui, fetchFn, csrf, reload }) {
  const { url, preset, label, tenant } = button.dataset;
  const answer = await ui.confirm("Apply this preset?", `"${label}" overrides the feature flags for ${tenant}.`, "Apply");
  if (!answer || !answer.isConfirmed) return false;
  let d = {};
  try {
    const response = await fetchFn(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": csrf() },
      body: new URLSearchParams({ preset }).toString(),
    });
    d = await response.json().catch(() => ({}));
  } catch (down) {
    ui.toast("Check the connection.", "error");
    return false;
  }
  if (!d.success) {
    ui.toast(d.error || "Could not apply the preset.", "error");
    return false;
  }
  ui.toast(`Applied: ${d.label}`, "success");
  reload();
  return true;
}
