/*
 * What a tap on the running order page does.
 *
 * `edit` is order_edit.js (its sheets ask why, and its post() asks for a
 * manager's PIN when one is needed); the sheets show their own errors and
 * resolve to null. A second tap while the first is still out is ignored,
 * so a dish is never cancelled or served twice by a double tap. The list
 * is asked for again after every action, except one that leaves the page.
 */
import { withId } from "../billing/urls.js";

export function createRunningOrderActions({ edit, toast, urls, orderId, refresh, go }) {
  let busy = false;

  async function once(work) {
    if (busy) return null;
    busy = true;
    let leaving = false;
    try {
      const result = await work(() => { leaving = true; });
      return result;
    } finally {
      busy = false;
      if (!leaving) refresh();
    }
  }

  return {
    busy: () => busy,
    reduce: (item) => once(() => edit.reduce(item)),
    remove: (item) => once(() => edit.remove(item)),
    comp: (item) => once(() => edit.complimentary(item)),
    serve: (id) => once(() => edit.post(withId(urls.serve, id), {}).catch((err) => {
      toast(err.message || "Could not serve this dish.", "error");
      return null;
    })),
    cancelOrder: () => once(async (leave) => {
      const data = await edit.cancelOrder(orderId, { billUrl: urls.bill });
      if (data) {
        leave();
        go(urls.tables);
      }
      return data;
    }),
  };
}
