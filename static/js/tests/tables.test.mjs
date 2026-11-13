/*
 * The floor plan's modules (static/js/tables/), with fakes for the server:
 * what the floor shows for each kind of table, what each action sends,
 * and what happens when the server refuses, the connection is down, an
 * answer arrives late, or a button is tapped twice.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createTableActions } from "../tables/actions.js";
import {
  bySection, canTransfer, failedHtml, floorHtml, manageRows, menuLink, moreSheet, pickerOptions, sheetsKey, summarise,
} from "../tables/floor.js";
import { createFloorLoader } from "../tables/loader.js";
import { fakeUi, networkDown, reply, scriptedFetch } from "./helpers.mjs";

const URLS = {
  tables_data: "/tables-data/",
  manage_table: "/manage-table/",
  clean_table: "/clean-table/0/",
  transfer_table: "/transfer-table/",
  merge_tables: "/merge-tables/",
  unmerge_tables: "/unmerge-tables/0/",
  generate_bill: "/generate-bill/0/",
  approve_items: "/approve-items/0/",
  billing: "/billing/",
  bill: "/bill/0/",
  running_order: "/order/0/",
  digital_menu: "/menu/digital-menu/",
};

const table = (id, status, extra = {}) => ({
  id, name: "T" + id, section: "", qr_token: "tok-" + id, status, order_id: null, cooking_items: 0, elapsed: 0,
  is_primary: false, merged_with_names: "", primary_table: null, primary_table_name: null,
  waiter_name: "", waiter_initials: "", ...extra,
});

const FLOOR = [
  table(1, "free"),
  table(2, "preparing", { order_id: 20, elapsed: 12, cooking_items: 2, waiter_name: "Asha Rao", waiter_initials: "AR" }),
  table(3, "billing", { order_id: 30, elapsed: 50 }),
  table(4, "cleaning"),
  table(5, "needs_approval", { order_id: 50, waiter_name: "Guest (QR)", waiter_initials: "QR" }),
  table(6, "merged", { primary_table: 2, primary_table_name: "T2" }),
  table(7, "served", { order_id: 70, elapsed: 31 }),
];

/* ---- what the floor shows ------------------------------------------------- */

test("the numbers: free, active, awaiting payment, stuck, needing approval", () => {
  assert.deepEqual(summarise(FLOOR), { total: 7, free: 1, active: 4, billing: 1, stuck: 1, approval: 1 });
  assert.deepEqual(summarise([]), { total: 0, free: 0, active: 0, billing: 0, stuck: 0, approval: 0 });
});

test("a table waiting to pay is not counted as stuck, however long it has waited", () => {
  assert.equal(summarise([table(3, "billing", { order_id: 30, elapsed: 500 })]).stuck, 0);
});

test("tables are grouped by section, the unnamed section first", () => {
  const grouped = bySection([table(1, "free", { section: "Patio" }), table(2, "free"), table(3, "free", { section: "Bar" }), table(4, "free", { section: "Patio" })]);
  assert.deepEqual(grouped.map(([name, list]) => [name, list.map((t) => t.id)]), [["", [2]], ["Bar", [3]], ["Patio", [1, 4]]]);
});

test("each kind of table gets its own buttons, by data-action and never onclick", () => {
  const html = floorHtml(FLOOR, URLS);
  assert.ok(!html.includes("onclick"));
  const cardOf = (id) => html.split('<div class="table-card').find((part) => part.includes(`data-table="${id}"`));
  assert.match(cardOf(1), /data-action="table\.go" data-url="\/billing\/\?table=1"/);
  assert.match(cardOf(2), /data-action="table\.bill" data-order="20"/);
  assert.match(cardOf(2), /data-action="table\.more" data-id="2"/);
  assert.match(cardOf(2), /2 in kitchen/);
  assert.match(cardOf(2), /title="Waiter: Asha Rao">AR</);
  assert.match(cardOf(3), /data-action="table\.go" data-url="\/bill\/30\/"/);
  assert.match(cardOf(4), /data-action="table\.clean" data-id="4"/);
  assert.match(cardOf(5), /data-action="table\.more" data-id="5"/);
  assert.match(cardOf(5), /waiter-badge qr-badge/);
  assert.match(cardOf(6), /data-url="\/billing\/\?table=2"[^>]*>Go to Primary \(T2\)/);
  assert.match(cardOf(7), /card-urgent/);
  assert.ok(!cardOf(2).includes("card-urgent") && !cardOf(2).includes("card-warn"));
  assert.match(floorHtml([table(8, "ordering", { order_id: 80, elapsed: 16 })], URLS), /card-warn/);
});

test("the strip says what needs a manager, or that all is clear", () => {
  const busy = floorHtml(FLOOR, URLS);
  assert.match(busy, /1 stuck &gt;30m/);
  assert.match(busy, /1 need approval/);
  assert.match(busy, /1 awaiting payment/);
  assert.ok(!busy.includes("All clear"));
  assert.match(floorHtml([table(1, "free")], URLS), /alert-strip clean.*All clear/s);
});

test("a primary table can be unmerged from its card when free and from its sheet when busy", () => {
  assert.match(floorHtml([table(1, "free", { is_primary: true, merged_with_names: "T2, T3" })], URLS), /Merged w\/ T2, T3.*data-action="table\.unmerge" data-id="1"/s);
  assert.match(moreSheet(table(2, "preparing", { order_id: 20, is_primary: true }), URLS).buttons, /data-action="table\.unmerge" data-id="2"/);
  assert.ok(!moreSheet(table(2, "preparing", { order_id: 20 }), URLS).buttons.includes("table.unmerge"));
});

test("names typed by people are escaped wherever they are drawn", () => {
  const evil = '<img src=x onerror="alert(1)">';
  const nasty = [table(1, "preparing", { name: evil, section: evil, order_id: 10, waiter_name: evil, waiter_initials: "<b" }),
    table(2, "merged", { primary_table: 1, primary_table_name: evil }),
    table(3, "free", { is_primary: true, merged_with_names: evil })];
  for (const html of [floorHtml(nasty, URLS), manageRows(nasty), Object.values(pickerOptions(nasty)).join(""), failedHtml(evil)]) {
    assert.ok(!html.includes("<img"), html);
    assert.ok(!html.includes('onerror="alert'), html);
  }
  assert.match(manageRows(nasty), /value="&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;"/);
});

test("the More sheet offers what fits the table", () => {
  const actionsIn = (t) => Array.from(moreSheet(t, URLS).buttons.matchAll(/data-action="([^"]+)"(?: data-(?:url|id|order)="([^"]*)")?/g)).map((m) => [m[1], m[2]]);
  assert.deepEqual(actionsIn(FLOOR[1]), [["table.go", "/billing/?table=2"], ["table.go", "/order/20/"], ["table.transfer", "2"], ["table.bill", "20"]]);
  assert.deepEqual(actionsIn(FLOOR[2]), [["table.go", "/bill/30/"], ["table.go", "/billing/?table=3"]]);
  assert.deepEqual(actionsIn(FLOOR[3]), [["table.clean", "4"]]);
  assert.deepEqual(actionsIn(FLOOR[4]), [["table.approve", "50"], ["table.go", "/order/50/"]]);
  assert.equal(moreSheet(FLOOR[4], URLS).note, "Customer added items via QR");
});

test("an order moves only to a free table, and only an open order can move", () => {
  assert.deepEqual(FLOOR.filter(canTransfer).map((t) => t.id), [2, 5, 7]);
  const options = pickerOptions(FLOOR);
  const ids = (html) => Array.from(html.matchAll(/value="(\d+)"/g)).map((m) => Number(m[1]));
  assert.deepEqual(ids(options.transferFrom), [2, 5, 7]);
  assert.deepEqual(ids(options.transferTo), [1]);
  assert.deepEqual(ids(options.mergeSecondary), [1]);
  assert.deepEqual(ids(options.mergePrimary), [1, 2, 3, 4, 5, 6, 7]);
});

test("with no free table the pickers say so instead of being empty", () => {
  const options = pickerOptions([table(2, "preparing", { order_id: 20 })]);
  assert.match(options.transferTo, /No free tables available right now/);
  assert.match(options.mergeSecondary, /No free tables available right now/);
  assert.match(pickerOptions([table(1, "free")]).transferFrom, /No table has an order to move/);
});

test("the sheets are redrawn only when what they show changes, so typing survives a refresh", () => {
  const before = sheetsKey(FLOOR);
  // a minute passes, the kitchen finishes a dish: the cards change, the sheets do not
  const later = FLOOR.map((t) => ({ ...t, elapsed: t.elapsed + 1, cooking_items: 0 }));
  assert.equal(sheetsKey(later), before);
  assert.notEqual(sheetsKey(FLOOR.map((t) => (t.id === 1 ? { ...t, name: "Window" } : t))), before);
  assert.notEqual(sheetsKey(FLOOR.map((t) => (t.id === 1 ? { ...t, status: "ordering" } : t))), before);
  assert.notEqual(sheetsKey(FLOOR.slice(1)), before);
});

test("a table's QR opens the guest menu with that table's token", () => {
  assert.equal(menuLink("https://cafe.rasova.net", "/menu/digital-menu/", "a b&c"), "https://cafe.rasova.net/menu/digital-menu/?table_token=a%20b%26c");
});

/* ---- asking for the floor -------------------------------------------------- */

function loaderRig(script) {
  const drawn = [];
  const failed = [];
  const fetchFn = scriptedFetch(script);
  const loader = createFloorLoader({ fetchFn, url: URLS.tables_data, onTables: (t) => drawn.push(t), onFailure: (m) => failed.push(m) });
  return { loader, drawn, failed, fetchFn };
}

test("the floor is fetched and handed over", async () => {
  const rig = loaderRig([reply(200, { tables: FLOOR })]);
  assert.equal(await rig.loader.refresh(), true);
  assert.equal(rig.fetchFn.calls[0].url, "/tables-data/");
  assert.deepEqual(rig.drawn, [FLOOR]);
  assert.equal(rig.loader.find("2").order_id, 20);
  assert.equal(rig.loader.find(99), undefined);
});

test("a failure says why: the server's message, a lost session, an error page, no connection", async () => {
  const rig = loaderRig([
    reply(500, { error: "tables_data_failed", message: "Could not load table data. Please try again." }),
    reply(200, undefined),
    reply(502, undefined),
    networkDown(),
  ]);
  for (let i = 0; i < 4; i += 1) assert.equal(await rig.loader.refresh(), false);
  assert.deepEqual(rig.failed, ["Could not load table data. Please try again.", "Please sign in again.", "Server error (502).", "Check the connection."]);
  assert.deepEqual(rig.drawn, []);
});

test("an older answer arriving after a newer one is not drawn", async () => {
  const drawn = [];
  const waiting = [];
  const fetchFn = () => new Promise((resolve) => waiting.push(resolve));
  const loader = createFloorLoader({ fetchFn, url: "/tables-data/", onTables: (t) => drawn.push(t), onFailure: () => drawn.push("failed") });
  const poll = loader.refresh();            // the 5-second poll, asked before the action
  const afterAction = loader.refresh();     // asked after the table was cleaned
  waiting[1](reply(200, { tables: [table(4, "free")] }));
  assert.equal(await afterAction, true);
  waiting[0](reply(200, { tables: [table(4, "cleaning")] }));
  assert.equal(await poll, false);
  assert.deepEqual(drawn.map((tables) => tables[0].status), ["free"]);
  assert.equal(loader.tables()[0].status, "free");
});

test("a late failure does not wipe a floor drawn since", async () => {
  const failed = [];
  const waiting = [];
  const fetchFn = () => new Promise((resolve, reject) => waiting.push({ resolve, reject }));
  const loader = createFloorLoader({ fetchFn, url: "/tables-data/", onTables: () => {}, onFailure: (m) => failed.push(m) });
  const first = loader.refresh();
  const second = loader.refresh();
  waiting[1].resolve(reply(200, { tables: [] }));
  await second;
  waiting[0].reject(networkDown());
  await first;
  assert.deepEqual(failed, []);
});

/* ---- the actions ----------------------------------------------------------- */

function fakeApi(script) {
  const calls = [];
  return {
    calls,
    post: async (url, body) => {
      calls.push({ url, body });
      const next = script.shift();
      if (next === undefined) throw new Error("unexpected post " + url);
      if (next instanceof Error) throw next;
      return typeof next === "function" ? next() : next;
    },
  };
}

function rig(script, { confirmed = true } = {}) {
  const api = fakeApi(script);
  const ui = fakeUi();
  const asked = [];
  ui.confirm = async (...args) => { asked.push(args); return { isConfirmed: confirmed }; };
  const seen = { went: [], refreshed: 0, closed: 0 };
  const actions = createTableActions({
    api, ui, urls: URLS,
    refresh: async () => { seen.refreshed += 1; },
    go: (url) => seen.went.push(url),
    closeSheets: () => { seen.closed += 1; },
  });
  return { actions, api, ui, asked, seen };
}

/* What core/http.js does on a refusal or offline: it has shown a toast, and throws. */
const refused = () => new Error("Table already occupied");

test("Bill makes the bill and goes to it", async () => {
  const r = rig([{ success: true, order_id: 20 }]);
  assert.equal(await r.actions.bill("20"), true);
  assert.deepEqual(r.api.calls, [{ url: "/generate-bill/20/", body: {} }]);
  assert.deepEqual(r.seen.went, ["/bill/20/"]);
});

test("Bill on a table with no order asks nothing of the server", async () => {
  const r = rig([]);
  assert.equal(await r.actions.bill(""), false);
  assert.deepEqual(r.api.calls, []);
  assert.deepEqual(r.ui.calls.toast, [["No active order for this table", "warning"]]);
});

test("a refused bill stays on the floor plan", async () => {
  const r = rig([refused()]);
  assert.equal(await r.actions.bill(20), false);
  assert.deepEqual(r.seen.went, []);
});

test("a second tap while the first is on its way is ignored", async () => {
  let answer;
  const r = rig([() => new Promise((resolve) => { answer = resolve; })]);
  const first = r.actions.bill(20);
  const second = r.actions.bill(20);
  assert.equal(await second, false);
  answer({ success: true, order_id: 20 });
  assert.equal(await first, true);
  assert.equal(r.api.calls.length, 1);
  assert.deepEqual(r.seen.went, ["/bill/20/"]);
});

test("after a refusal the button works again", async () => {
  const r = rig([refused(), { success: true }]);
  assert.equal(await r.actions.clean(4), false);
  assert.equal(await r.actions.clean(4), true);
  assert.equal(r.api.calls.length, 2);
});

test("Mark Clean frees the table, closes the sheet and redraws the floor", async () => {
  const r = rig([{ success: true }]);
  await r.actions.clean("4");
  assert.deepEqual(r.api.calls, [{ url: "/clean-table/4/", body: {} }]);
  assert.deepEqual(r.ui.calls.toast, [["Table Cleaned", "success"]]);
  assert.deepEqual([r.seen.closed, r.seen.refreshed], [1, 1]);
});

test("offline or refused, nothing is redrawn and no success is claimed", async () => {
  const r = rig([new Error("Offline")]);
  assert.equal(await r.actions.clean(4), false);
  assert.deepEqual(r.ui.calls.toast, []);
  assert.deepEqual([r.seen.closed, r.seen.refreshed], [0, 0]);
});

test("Unmerge asks first, in the page's own dialog", async () => {
  const no = rig([], { confirmed: false });
  await no.actions.unmerge(2);
  assert.equal(no.asked.length, 1);
  assert.deepEqual(no.api.calls, []);

  const yes = rig([{ success: true }]);
  await yes.actions.unmerge("2");
  assert.deepEqual(yes.api.calls, [{ url: "/unmerge-tables/2/", body: {} }]);
  assert.deepEqual(yes.ui.calls.toast, [["Tables Unmerged", "success"]]);
  assert.equal(yes.seen.refreshed, 1);
});

test("Approve asks first, then sends the QR order to the kitchen", async () => {
  const no = rig([], { confirmed: false });
  await no.actions.approve(50);
  assert.deepEqual(no.api.calls, []);

  const yes = rig([{ success: true, count: 3 }]);
  await yes.actions.approve("50");
  assert.deepEqual(yes.api.calls, [{ url: "/approve-items/50/", body: {} }]);
  assert.deepEqual([yes.seen.closed, yes.seen.refreshed], [1, 1]);
});

test("Combine sends the two tables as numbers, and refuses a table with itself", async () => {
  const r = rig([{ success: true, merge_id: 1 }]);
  assert.equal(await r.actions.merge("2", "2"), false);
  assert.equal(await r.actions.merge("2", ""), false);
  assert.deepEqual(r.api.calls, []);
  assert.deepEqual(r.ui.calls.toast.map(([message]) => message), ["Cannot merge table with itself", "Pick both tables to combine."]);
  assert.equal(await r.actions.merge("2", "1"), true);
  assert.deepEqual(r.api.calls, [{ url: "/merge-tables/", body: { primary_table: 2, tables: [1] } }]);
});

test("Transfer moves the order sitting at the source table", async () => {
  const r = rig([{ success: true, order_id: 20 }]);
  assert.equal(await r.actions.transfer("2", "1", FLOOR), true);
  assert.deepEqual(r.api.calls, [{ url: "/transfer-table/", body: { order_id: 20, table_id: 1 } }]);
  assert.deepEqual(r.ui.calls.toast, [["Table Transferred", "success"]]);
});

test("Transfer with no source, no destination or an empty table says what is missing", async () => {
  const r = rig([]);
  assert.equal(await r.actions.transfer("", "1", FLOOR), false);
  assert.equal(await r.actions.transfer("2", "", FLOOR), false);
  assert.equal(await r.actions.transfer("1", "4", FLOOR), false);     // table 1 is free: no order to move
  assert.deepEqual(r.api.calls, []);
  assert.equal(r.ui.calls.toast.length, 3);
});

test("a new table needs a name; a saved one keeps the floor in step", async () => {
  const r = rig([{ success: true }, { success: true }]);
  assert.equal(await r.actions.create("   ", "Patio"), false);
  assert.equal(await r.actions.create(" T-12 ", " Patio "), true);
  assert.equal(await r.actions.save("3", "Window", ""), true);
  assert.equal(await r.actions.save("3", "", "Bar"), false);
  assert.deepEqual(r.api.calls, [
    { url: "/manage-table/", body: { action: "create", name: "T-12", section: "Patio" } },
    { url: "/manage-table/", body: { action: "edit", table_id: 3, name: "Window", section: "" } },
  ]);
  assert.equal(r.seen.refreshed, 2);
  assert.equal(r.seen.closed, 0, "the Tables sheet stays open while tables are being added");
});
