/*
 * Opening and closing the cash register (shifts/cash_sessions.html). The
 * button names its address (data-url), the field the server reads
 * (data-field) and the input holding the amount (data-input).
 * Resolves to true when the server took it (the page then reloads).
 */
export async function submitSession(button, { api, ui, doc, reload }) {
  const input = doc.getElementById(button.dataset.input);
  const body = { [button.dataset.field]: input ? input.value : "" };
  let data;
  try {
    data = await api.post(button.dataset.url, body);
  } catch (shown) {
    return false;               // the client showed the error
  }
  if (data && data.error) {
    ui.toast(data.error, "error");
    return false;
  }
  reload();
  return true;
}
