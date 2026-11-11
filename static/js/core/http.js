/*
 * How a screen talks to the server.
 *
 *   const data = await api.post("/pay/42/", { method: "cash", amount: "100" });
 *
 * Every call sends the CSRF token and JSON, shows the loading bar, turns a
 * server error into a toast the cashier can read, and throws so the caller
 * can stop. A change that needs a manager's PIN (a printed bill going down)
 * is asked for and sent again through static/js/approval.js.
 *
 * Offline, a request is refused up front with a toast: the screens that
 * can work offline (billing) queue their work in core/offline.js instead.
 *
 * `createClient` takes its dependencies as arguments so node can test the
 * behaviour with a fake fetch; app.js builds the real one.
 */
import { csrfToken } from "./csrf.js";

export function createClient({
  fetchFn = (...args) => globalThis.fetch(...args),
  csrf = () => csrfToken(),
  ui,
  approval = () => globalThis.RasovaApproval,
  isOnline = () => globalThis.navigator.onLine,
} = {}) {
  async function request(endpoint, options = {}) {
    if (!isOnline()) {
      ui.toast("Action prevented: You are offline.", "error");
      throw new Error("Offline");
    }

    ui.showLoading();
    const headers = {
      "Content-Type": "application/json",
      "X-CSRFToken": csrf(),
      ...options.headers,
    };

    try {
      const send = (extra) => fetchFn(endpoint, { ...options, headers: { ...headers, ...extra } });
      const gate = approval();
      const response = gate ? await gate.send(send) : await send({});

      let data;
      try {
        data = await response.json();
      } catch (notJson) {
        // An HTML page came back (a 404 or 500 page, a proxy error): say so,
        // or a button posting to a wrong address simply does nothing.
        const message = `Server Error (${response.status}). Please try again.`;
        ui.toast(message, "error");
        throw new Error(message);
      }

      if (!response.ok || data.success === false) {
        const message = data.error || data.detail || `Server Error (${response.status})`;
        ui.toast(message, "error");
        throw new Error(message);
      }
      return data;
    } catch (err) {
      // fetch itself failed (no route to the server): one generic toast.
      if (err.message !== "Offline" && err.name === "TypeError") {
        ui.toast("Network failure or server is unreachable.", "error");
      }
      throw err;
    } finally {
      ui.hideLoading();
    }
  }

  return {
    getCsrf: () => csrf(),
    request,
    get: (endpoint) => request(endpoint, { method: "GET" }),
    post: (endpoint, body) => request(endpoint, { method: "POST", body: JSON.stringify(body) }),
    put: (endpoint, body) => request(endpoint, { method: "PUT", body: JSON.stringify(body) }),
    delete: (endpoint) => request(endpoint, { method: "DELETE" }),
  };
}
