/*
 * The search box and category list above an inventory report's table
 * (inventory/consumption_report.html, variance_report.html): rows not
 * matching are hidden, and the count of matches is shown while a filter
 * is on. Each row carries data-name (lower case) and data-category.
 */
export function filterRows(rows, { term = "", category = "" } = {}) {
  const t = term.trim().toLowerCase();
  let visible = 0;
  for (const row of rows) {
    const show = (!t || (row.dataset.name || "").includes(t)) && (!category || (row.dataset.category || "") === category);
    row.classList.toggle("row-hidden", !show);
    if (show) visible += 1;
  }
  return visible;
}

export function countText(visible, { term = "", category = "" } = {}) {
  return term.trim() || category ? `${visible} matching` : "";
}

if (typeof document !== "undefined") {
  const search = document.getElementById("itemSearch");
  const pick = document.getElementById("categoryFilter");
  const count = document.getElementById("filterCount");
  const apply = () => {
    const filter = { term: search ? search.value : "", category: pick ? pick.value : "" };
    const visible = filterRows(Array.from(document.querySelectorAll(".table tbody tr")), filter);
    if (count) count.textContent = countText(visible, filter);
  };
  if (search) search.addEventListener("input", apply);
  if (pick) pick.addEventListener("change", apply);
}
