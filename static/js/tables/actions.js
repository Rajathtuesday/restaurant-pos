/*
 * What a tap on the floor plan asks the server to do: make the bill, mark
 * a table clean, combine and split tables, move an order, approve what a
 * guest ordered by QR, add or rename a table.
 *
 * `api` (core/http.js) shows the server's refusal, or "you are offline",
 * as a toast and throws; an action then simply stops and the floor stays
 * as it was. While an action is on its way a second tap on the same thing
 * is ignored. Everything it needs comes in as an argument, so node can
 * test it with fakes.
 */
import { withId } from "../billing/urls.js";

const whole = (value) => {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : null;
};

export function createTableActions({ api, ui, urls, refresh, go, closeSheets = () => {} }) {
  const busy = new Set();

  /* Run `work` unless the same thing is already running. True if it finished. */
  async function once(key, work) {
    if (busy.has(key)) return false;
    busy.add(key);
    try {
      await work();
      return true;
    } catch (shown) {
      return false;                       // api has shown the error
    } finally {
      busy.delete(key);
    }
  }

  async function done(message) {
    closeSheets();
    ui.toast(message, "success");
    await refresh();
  }

  return {
    /* Make the bill for an order and go to it. */
    bill(orderId) {
      if (!whole(orderId)) {
        ui.toast("No active order for this table", "warning");
        return Promise.resolve(false);
      }
      return once("bill:" + orderId, async () => {
        const d = await api.post(withId(urls.generate_bill, orderId), {});
        go(withId(urls.bill, d.order_id || orderId));
      });
    },

    clean(tableId) {
      return once("clean:" + tableId, async () => {
        await api.post(withId(urls.clean_table, tableId), {});
        await done("Table Cleaned");
      });
    },

    unmerge(primaryId) {
      return once("unmerge:" + primaryId, async () => {
        const answer = await ui.confirm("Unmerge these tables?", "Every table linked to this one becomes its own table again.", "Unmerge");
        if (!answer || !answer.isConfirmed) return;
        await api.post(withId(urls.unmerge_tables, primaryId), {});
        await done("Tables Unmerged");
      });
    },

    merge(primary, secondary) {
      const p = whole(primary);
      const s = whole(secondary);
      if (!p || !s) {
        ui.toast("Pick both tables to combine.", "warning");
        return Promise.resolve(false);
      }
      if (p === s) {
        ui.toast("Cannot merge table with itself", "warning");
        return Promise.resolve(false);
      }
      return once("merge", async () => {
        await api.post(urls.merge_tables, { primary_table: p, tables: [s] });
        await done("Tables Combined");
      });
    },

    /* Move the order sitting at `fromTableId` to `toTableId`. */
    transfer(fromTableId, toTableId, tables) {
      const from = tables.find((t) => t.id === whole(fromTableId));
      const to = whole(toTableId);
      if (!from || !from.order_id || !to) {
        ui.toast("Pick the table to move and where it goes.", "warning");
        return Promise.resolve(false);
      }
      return once("transfer", async () => {
        await api.post(urls.transfer_table, { order_id: from.order_id, table_id: to });
        await done("Table Transferred");
      });
    },

    approve(orderId) {
      return once("approve:" + orderId, async () => {
        const answer = await ui.confirm("Approve all items?", "Everything this table ordered by QR goes to the kitchen.", "Approve");
        if (!answer || !answer.isConfirmed) return;
        await api.post(withId(urls.approve_items, orderId), {});
        await done("Items Approved & Sent to Kitchen");
      });
    },

    /* Add a table. Resolves true when it was added, so the form can be cleared. */
    create(name, section) {
      const typed = (name || "").trim();
      if (!typed) {
        ui.toast("Enter table name", "warning");
        return Promise.resolve(false);
      }
      return once("create", async () => {
        await api.post(urls.manage_table, { action: "create", name: typed, section: (section || "").trim() });
        ui.toast("Table added", "success");
        await refresh();
      });
    },

    save(tableId, name, section) {
      const typed = (name || "").trim();
      if (!typed) {
        ui.toast("A table needs a name.", "warning");
        return Promise.resolve(false);
      }
      return once("save:" + tableId, async () => {
        await api.post(urls.manage_table, { action: "edit", table_id: whole(tableId), name: typed, section: (section || "").trim() });
        ui.toast("Table saved!", "success");
        await refresh();
      });
    },
  };
}
