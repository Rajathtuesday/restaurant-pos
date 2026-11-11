import assert from "node:assert/strict";
import { test } from "node:test";

import { createClient } from "../core/http.js";
import { fakeUi, networkDown, reply, scriptedFetch } from "./helpers.mjs";

function client(script, extra = {}) {
  const ui = fakeUi();
  const fetchFn = scriptedFetch(script);
  const api = createClient({
    fetchFn, ui, csrf: () => "tok", approval: () => null, isOnline: () => true, ...extra,
  });
  return { api, ui, fetchFn };
}

test("a good answer comes back as data, with CSRF and JSON headers", async () => {
  const { api, ui, fetchFn } = client([reply(200, { success: true, order_id: 7 })]);
  const data = await api.post("/create-order/", { cart: [] });
  assert.equal(data.order_id, 7);
  const { url, options } = fetchFn.calls[0];
  assert.equal(url, "/create-order/");
  assert.equal(options.method, "POST");
  assert.equal(options.headers["X-CSRFToken"], "tok");
  assert.equal(options.headers["Content-Type"], "application/json");
  assert.equal(options.body, JSON.stringify({ cart: [] }));
  assert.deepEqual(ui.calls.toast, []);
  assert.equal(ui.calls.loading, 0, "the loading bar was taken down again");
});

test("offline: refused before any request, with a toast", async () => {
  const { api, ui, fetchFn } = client([], { isOnline: () => false });
  await assert.rejects(api.get("/x/"), /Offline/);
  assert.equal(fetchFn.calls.length, 0);
  assert.deepEqual(ui.calls.toast, [["Action prevented: You are offline.", "error"]]);
});

test("a server error with a message shows that message and throws it", async () => {
  const { api, ui } = client([reply(400, { error: "Amount required" })]);
  await assert.rejects(api.post("/pay/1/", {}), /Amount required/);
  assert.deepEqual(ui.calls.toast, [["Amount required", "error"]]);
});

test("a 200 that says success: false is an error too", async () => {
  const { api, ui } = client([reply(200, { success: false, error: "Name cannot be empty" })]);
  await assert.rejects(api.post("/rename/", {}), /Name cannot be empty/);
  assert.equal(ui.calls.toast[0][0], "Name cannot be empty");
});

test("an HTML page instead of JSON (a 500 page, a proxy) names the status", async () => {
  const { api, ui } = client([reply(502)]);
  await assert.rejects(api.get("/x/"), /Server Error \(502\)/);
  assert.deepEqual(ui.calls.toast, [["Server Error (502). Please try again.", "error"]]);
});

test("fetch itself failing is one network toast", async () => {
  const { api, ui } = client([networkDown()]);
  await assert.rejects(api.get("/x/"), TypeError);
  assert.deepEqual(ui.calls.toast, [["Network failure or server is unreachable.", "error"]]);
  assert.equal(ui.calls.loading, 0);
});

test("the manager-PIN gate wraps the request when it is present", async () => {
  const seen = [];
  const approval = () => ({
    send: async (send) => {
      seen.push("asked");
      return send({ "X-Approver-Id": "3", "X-Approver-Pin": "1234" });
    },
  });
  const { api, fetchFn } = client([reply(200, { success: true })], { approval });
  await api.post("/apply-discount/1/", { value: 10 });
  assert.deepEqual(seen, ["asked"]);
  assert.equal(fetchFn.calls[0].options.headers["X-Approver-Pin"], "1234");
  assert.equal(fetchFn.calls[0].options.headers["X-CSRFToken"], "tok", "the gate adds headers, it does not replace them");
});
