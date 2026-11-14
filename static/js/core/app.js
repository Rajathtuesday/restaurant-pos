/*
 * What base.html runs on every page.
 *
 * Builds the shared pieces (theme, toasts, the server client, the offline
 * queues, the notification poll, the installed-app bits) from what the
 * server put in the rasova-data block, wires the header buttons, and
 * publishes the shared objects on window for the pages that have not been
 * turned into modules yet.
 *
 * Bridge, to be removed: window.ui, window.apiClient, window.themeManager,
 * window.offlineQueue, window.offlinePaymentQueue, window.rasovaPlatform,
 * window.updateOfflineBanner, window.refreshNotifications. A page module
 * imports these from core/ instead; when the last template stops using the
 * globals, this block goes (core/tests/test_frontend_hygiene.py counts the
 * templates that still do).
 */
import { setupChecklist } from "./checklist.js";
import { delegate, onReady } from "./dom.js";
import { createClient } from "./http.js";
import { createNotificationPoller } from "./notifications.js";
import {
  ORDER_STORE, PAYMENT_STORE, createOfflineBanner, createOrderQueue, createPaymentQueue, indexedDbStore,
} from "./offline.js";
import { pageActions } from "./page_actions.js";
import { pageData } from "./page_data.js";
import { detectPlatform, startAndroidPrinting } from "./platform.js";
import { createPrinterChip } from "./printing_status.js";
import { registerServiceWorker, setupInstallPrompt } from "./pwa.js";
import { createThemeManager } from "./theme.js";
import { createUi } from "./toast.js";
import { csrfToken } from "./csrf.js";

const data = pageData();
const isOnline = () => navigator.onLine;

const theme = createThemeManager();
const ui = createUi({ isDark: theme.isDark });
const api = createClient({ ui, isOnline, csrf: () => csrfToken() });
const platform = detectPlatform({ agentHost: data.agent_host });

// The banner reads the queues when it updates, so the list can be filled
// after the queues exist (they need the banner's update as their onChange).
const queues = [];
const offlineBanner = createOfflineBanner({ el: document.getElementById("offline-banner"), queues, isOnline });
const queueOptions = { fetchFn: (...a) => fetch(...a), csrf: () => csrfToken(), ui, isOnline, onChange: () => offlineBanner.update() };
const orderQueue = createOrderQueue({ store: indexedDbStore(ORDER_STORE), ...queueOptions });
const paymentQueue = createPaymentQueue({ store: indexedDbStore(PAYMENT_STORE), ...queueOptions });
queues.push(orderQueue, paymentQueue);

// The printer chip, on the pages that show one (billing, tables, tokens).
const printerChip = createPrinterChip({ el: document.getElementById("printer-chip"), platform });
const poller = createNotificationPoller({ ui, onData: (answer) => printerChip.update(answer.printing) });

theme.init();

window.addEventListener("online", () => {
  ui.toast("Connection restored", "success");
  orderQueue.flush();
  paymentQueue.flush();
});
window.addEventListener("offline", () => {
  ui.toast("You are offline. Please check your connection.", "error");
  offlineBanner.update();
});

// The header, the banners and the shared page buttons: one listener, by data-action.
delegate(document.body, {
  ...pageActions(window),
  "theme.toggle": () => theme.toggle(),
  "printer.explain": () => {
    const said = printerChip.current();
    if (said) ui.toast(said.hint, said.level === "ok" ? "success" : said.level === "bad" ? "error" : "info");
  },
  "banner.dismiss": (el) => {
    const banner = document.getElementById(el.dataset.target);
    if (banner) banner.remove();
    if (el.dataset.remember) sessionStorage.setItem(el.dataset.remember, "1");
  },
});

onReady(() => {
  // The demo banner, once dismissed, stays away for the session.
  if (sessionStorage.getItem("demoTrailerBannerDismissed") === "1") {
    document.getElementById("demo-trailer-banner")?.remove();
  }
  offlineBanner.update();
  if (isOnline()) {
    orderQueue.flush();
    paymentQueue.flush();
  }
  if (data.authenticated) poller.start();
  setupChecklist();
});

registerServiceWorker();
if (data.authenticated) {
  startAndroidPrinting({ platform, serverUrl: data.server_url, agentKey: data.agent_key, ui });
  setupInstallPrompt({
    platform, serverUrl: data.server_url, agentKey: data.agent_key, demoSession: Boolean(data.demo_session),
  });
}

// The bridge (see the note at the top).
window.ui = ui;
window.apiClient = api;
window.themeManager = theme;
window.offlineQueue = orderQueue;
window.offlinePaymentQueue = paymentQueue;
window.rasovaPlatform = platform;
window.updateOfflineBanner = () => offlineBanner.update();
window.refreshNotifications = () => poller.poll();

export { api, ui, theme, platform, orderQueue, paymentQueue };
