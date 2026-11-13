/*
 * What a tap on the kitchen display asks the server to do: start a dish,
 * mark it ready, bump a whole ticket, clear the screen.
 *
 * `api` (core/http.js) shows the server's refusal, or "you are offline",
 * as a toast and throws; an action then simply stops and the board stays
 * as it was. While an action is on its way a second tap on the same thing
 * is ignored (a gloved knuckle often lands twice). Everything it needs
 * comes in as an argument, so node can test it with fakes.
 */
import { withId } from "../billing/urls.js";

export function createKitchenActions({ api, ui, urls, refresh }) {
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

  const step = (key, address) => once(key, async () => {
    await api.post(address, {});
    await refresh();
  });

  return {
    start: (itemId) => step("item:" + itemId, withId(urls.item_start, itemId)),
    ready: (itemId) => step("item:" + itemId, withId(urls.mark_ready, itemId)),
    bump: (kotId) => step("kot:" + kotId, withId(urls.bump_kot, kotId)),

    /* Owner and manager only (the server checks): every ticket off the screen. */
    clearAll() {
      return once("clear", async () => {
        const answer = await ui.confirm(
          "Clear every ticket off this screen?",
          "This marks every item currently shown as served. It is for wiping out stale or orphaned tickets, not for real service. This cannot be undone.",
          "Yes, clear all",
        );
        if (!answer || !answer.isConfirmed) return;
        const d = await api.post(urls.clear_all, {});
        ui.toast(`Cleared ${d.count} item(s)`, "success");
        await refresh();
      });
    },
  };
}
