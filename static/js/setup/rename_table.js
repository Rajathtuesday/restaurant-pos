/*
 * Renaming a table (Setup, tables): the new name is asked in Rasova's own
 * sheet (ui.ask). The browser's prompt() never shows in the Android app,
 * so renaming did nothing there. The chip carries its address (data-url)
 * and its name (data-name). Resolves to true when renamed.
 */
export async function renameTable(chip, { ui, api, reload }) {
  const name = await ui.ask("Rename table", { value: chip.dataset.name, label: "Table name", confirmText: "Rename" });
  if (!name) return false;
  let data;
  try {
    data = await api.post(chip.dataset.url, { name });
  } catch (shown) {
    return false;               // the client showed the error
  }
  if (!data || !data.success) {
    ui.toast((data && data.error) || "Could not rename the table.", "error");
    return false;
  }
  reload();
  return true;
}
