/*
 * Printing a bill from the bill page.
 *
 * After a payment the bill goes to the first printer that takes it:
 *   1. on Android (the Rasova app, or a phone's browser) the server's
 *      print queue, which the on-device agent polls: the cloud server
 *      cannot reach a printer on the restaurant's network itself
 *   2. the PC agent over its WebSocket, when the outlet uses it and a
 *      printer is named (a USB printer, with real cuts)
 *   3. the server sending ESC/POS straight to a network printer
 * If none did, the caller reloads the page with ?print=1 and the receipt
 * page opens for the browser to print.
 *
 * The Print button is simpler: the queue on Android, the browser's own
 * print elsewhere.
 */
import { withId } from "../billing/urls.js";

const AGENT_PORT = 8765;

export function createBillPrinter({
  platform, urls, csrf, ui, settings,
  fetchFn = (...a) => fetch(...a), win = globalThis, Socket = globalThis.WebSocket,
}) {
  const jsonPost = (url, body) => fetchFn(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  /* The server's queue. Says what happened; true if the job was taken. */
  async function queue(orderId, failText) {
    try {
      const resp = await jsonPost(urls.print_queue_add, { order_id: orderId });
      const d = await resp.json().catch(() => ({}));
      if (resp.ok && d.success) {
        ui.toast("Sent to printer", "success");
        return true;
      }
      ui.toast(d.error || failText, "error");
    } catch (gone) {
      ui.toast("Network error reaching printer", "error");
    }
    return false;
  }

  function connectAgent() {
    return new Promise((resolve, reject) => {
      const ws = new Socket(`ws://${platform.agentHost}:${AGENT_PORT}`);
      const timer = setTimeout(() => {
        ws.close();
        reject(new Error("timeout"));
      }, 2000);
      ws.onopen = () => {
        clearTimeout(timer);
        resolve(ws);
      };
      ws.onerror = () => {
        clearTimeout(timer);
        reject(new Error("not running"));
      };
    });
  }

  /* The PC agent. True only when it answered "ok" for this job. */
  async function throughAgent(orderId) {
    let ws;
    try {
      ws = await connectAgent();
      const resp = await fetchFn(withId(urls.qz_receipt_data, orderId) + "?split=" + (settings.splitMode ? 1 : 0));
      if (!resp.ok) return false;
      const { lines } = await resp.json();
      return await new Promise((resolve) => {
        const jobId = String(Date.now());
        const timer = setTimeout(() => resolve(false), 10000);
        ws.onmessage = (event) => {
          const msg = JSON.parse(event.data);
          if (msg.job_id !== jobId) return;
          clearTimeout(timer);
          resolve(msg.type === "ok");
        };
        ws.send(JSON.stringify({
          type: "print", printer: settings.printerName, lines, job_id: jobId, encoding: "cp437",
          outlet_id: String(settings.outletId), printer_mac: settings.printerMac || "",
        }));
      });
    } catch (noAgent) {
      return false;
    } finally {
      if (ws) ws.close();
    }
  }

  async function throughServer() {
    try {
      const resp = await jsonPost(settings.splitMode ? urls.print_split_bill : urls.print_bill);
      const d = await resp.json();
      return !!(resp.ok && d.success);
    } catch (gone) {
      return false;
    }
  }

  return {
    /* Right after a payment. Returns whether a printer took the bill. */
    async afterPayment(orderId) {
      if (platform.nativeAndroid || platform.android) {
        if (await queue(orderId, "Printer queue failed")) return true;
      }
      if (settings.useAgent && settings.printerName && await throughAgent(orderId)) return true;
      return throughServer();
    },

    /* The Print button. */
    async manual(orderId) {
      if (platform.nativeAndroid || platform.android) {
        await queue(orderId, "Could not reach printer");
        return;
      }
      win.print();
    },

    /* The receipt page in a small window, or in this one if popups are blocked. */
    openReceipt(orderId) {
      const url = withId(urls.thermal_receipt, orderId);
      const popup = win.open(url, "_blank", "width=440,height=700,menubar=no,toolbar=no,location=no,status=no");
      if (!popup || popup.closed) win.location.href = url;
    },
  };
}
