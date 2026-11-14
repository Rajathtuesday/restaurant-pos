/*
 * One feature switch on the control panel's feature page
 * (accounts/feature_flags.html). The switch carries its address
 * (data-url), the restaurant (data-tenant) and the feature (data-feature).
 * While it saves, the card says so and the switch is locked; a refusal
 * puts the switch back. The card's badge then says where the value comes
 * from now (an override, or the default for the restaurant's type).
 */
export async function toggleFeature(box, { api, ui, doc }) {
  const { url, tenant, feature } = box.dataset;
  if (!tenant) return false;
  const enabled = box.checked;
  const card = doc.getElementById("card-" + feature);
  card.classList.add("saving");
  box.disabled = true;
  let data;
  try {
    data = await api.post(url, { tenant_id: Number(tenant), feature, enabled });
  } catch (shown) {
    data = null;                   // the client showed why
  } finally {
    card.classList.remove("saving");
    box.disabled = false;
  }
  if (!data || data.error) {
    if (data && data.error) ui.toast("Error: " + data.error, "error");
    box.checked = !enabled;
    return false;
  }
  card.classList.toggle("is-off", !enabled);
  const badge = doc.getElementById("badge-" + feature);
  badge.className = "src-badge " + data.source;
  badge.textContent = data.source === "override" ? "Override" : "Default";
  ui.toast((enabled ? "Enabled" : "Disabled") + ": " + feature.replace(/_/g, " "), "success");
  return true;
}
