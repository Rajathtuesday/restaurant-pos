/*
 * Getting paper out of a billing screen.
 *
 * Three roads, picked by where Rasova runs (core/platform.js):
 *   iOS       no agent: the bill page opens in a tab for AirPrint
 *   Android   the job goes to the server's print queue; the on-device
 *             service (the Rasova app, or the Termux agent) polls and prints
 *   desktop   the receipt lines are fetched and handed to the local agent
 *             over its WebSocket, straight to the printer on port 9100
 */
import { withId } from "./urls.js";

const AGENT_PORT = 8765;

export function createPrinter({ platform, urls, csrf, fetchFn = (...a) => fetch(...a), win = globalThis, stationPrinterIp = "", outletId = "" }) {
  async function queueOnServer(orderId) {
    const resp = await fetchFn(urls.print_queue_add, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ order_id: orderId }),
    });
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      throw new Error(d.error || "Queue failed");
    }
  }

  function printThroughAgent(lines, orderId) {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`ws://${platform.agentHost}:${AGENT_PORT}`);
      const jobId = "bill-" + orderId + "-" + Date.now();
      const timer = setTimeout(() => {
        ws.close();
        reject(new Error("timeout"));
      }, 10000);
      ws.onopen = () => ws.send(JSON.stringify({
        type: "print", job_id: jobId, lines,
        outlet_id: String(outletId), network_host: stationPrinterIp, network_port: 9100,
      }));
      ws.onmessage = (e) => {
        clearTimeout(timer);
        const d = JSON.parse(e.data);
        if (d.job_id !== jobId) return;
        ws.close();
        d.type === "ok" ? resolve() : reject(new Error(d.message || "print-failed"));
      };
      ws.onerror = () => {
        clearTimeout(timer);
        reject(new Error("agent-offline"));
      };
    });
  }

  return {
    /* The bill, right after a counter payment. Throws when nothing printed. */
    async printBill(orderId) {
      if (platform.ios) {
        win.open(withId(urls.bill_view, orderId) + "?print=1", "_blank");
        return;
      }
      if (platform.android) {
        await queueOnServer(orderId);
        return; // the agent prints within a couple of seconds
      }
      const resp = await fetchFn(withId(urls.qz_receipt_data, orderId), { headers: { "X-CSRFToken": csrf() } });
      const data = await resp.json();
      if (!data.success || !data.lines) throw new Error("receipt-data-failed");
      await printThroughAgent(data.lines, orderId);
    },

    /* The thermal receipt page in a small window: the fallback everywhere. */
    openReceipt(orderId) {
      const url = withId(urls.thermal_receipt, orderId);
      const popup = win.open(url, "_blank", "width=420,height=680,menubar=no,toolbar=no,location=no,status=no");
      return { url, blocked: !popup || popup.closed };
    },

    /* A token order's receipt: the queue on Android, the popup elsewhere. */
    async printReceipt(orderId) {
      if (platform.nativeAndroid || platform.android) {
        await queueOnServer(orderId);
        return { queued: true };
      }
      return this.openReceipt(orderId);
    },
  };
}
