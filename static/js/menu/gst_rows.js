/*
 * GST rates page helpers (menu/gst_management.html), kept apart from the
 * page wiring (gst.js) so node can test them.
 */
export function clearRetired(doc, itemIds) {
  for (const id of itemIds) {
    doc.getElementById(`retired-${id}`)?.remove();
    doc.querySelector(`#gst-${id} option[disabled]`)?.remove();
  }
  if (!doc.querySelector('.retired-tag[id^="retired-"]')) doc.getElementById("retired-banner")?.remove();
}

export function setCollapsed(doc, categoryId, collapsed) {
  doc.getElementById(`cat-${categoryId}`).hidden = collapsed;
  doc.getElementById(`card-${categoryId}`).classList.toggle("collapsed", collapsed);
  doc.querySelector(`[data-action="gst.collapse"][data-category="${categoryId}"]`)?.setAttribute("aria-expanded", collapsed ? "false" : "true");
}
