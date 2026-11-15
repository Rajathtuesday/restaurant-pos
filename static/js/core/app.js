/*
 * What base.html runs on every page.
 *
 * Builds the shared pieces (theme, toasts, the server client, the offline
 * queues, the notification poll, the installed-app bits) from what the
 * server put in the rasova-data block and wires the header buttons. A page
 * module imports the shared objects from here (the last line lists them);
 * nothing is published on window (core/tests/test_static_modules.py).
 */
import { setupChecklist } from "./checklist.js";
import { delegate, onReady } from "./dom.js";
import { createClient } from "./http.js";
import { createNotificationPoller } from "./notifications.js";
import {
  ORDER_STORE, PAYMENT_STORE, createOfflineBanner, createOrderQueue, createPaymentQueue, indexedDbStore,
} from "./offline.js";
import { pageActions, pageChangeActions, pageInputActions, pageSubmitActions } from "./page_actions.js";
import { pageData } from "./page_data.js";
import { createPostActions } from "./post_actions.js";
import { detectPlatform, startAndroidPrinting } from "./platform.js";
import { createPrinterChip } from "./printing_status.js";
import { registerServiceWorker, setupInstallPrompt } from "./pwa.js";
import { createThemeManager } from "./theme.js";
import { configure as configureOrderEdit } from "../order_edit.js";
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
configureOrderEdit({ ui });

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

// Buttons and forms that post and reload (core/post_actions.js).
const posts = createPostActions({ api, ui, doc: document, reload: () => window.location.reload() });

// The header, the banners and the shared page buttons: one listener, by data-action.
delegate(document.body, {
  ...pageActions(window),
  post: (el) => posts.click(el),
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

delegate(document.body, { post: (form, event) => posts.submit(form, event), ...pageSubmitActions(ui) }, "submit");
delegate(document.body, pageChangeActions(), "change");
delegate(document.body, pageInputActions(), "input");

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

export { api, ui, theme, platform, orderQueue, paymentQueue };
