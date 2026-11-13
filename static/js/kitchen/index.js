/*
 * The kitchen display: every ticket with unfinished dishes as a card,
 * refreshed every few seconds, filtered by station.
 *
 * The page gives this module its addresses in one json_script block,
 * page-data (kitchen.views.kitchen_page_data). Buttons carry data-action;
 * this is the one place that handles them.
 */
import { api, ui } from "../core/app.js";
import { delegate, onReady, qsa } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { createKitchenActions } from "./actions.js";
import { boardHtml, failedHtml } from "./board.js";
import { createKitchenLight } from "./light.js";
import { createKitchenLoader } from "./loader.js";

const data = pageData("page-data");
const grid = document.getElementById("orders");

/* The same tickets a minute later read differently (the timers), so the
   markup is compared, and the grid is only touched when it changed. */
let drawn = null;

const loader = createKitchenLoader({
  fetchFn: (...args) => fetch(...args),
  url: data.urls.kitchen_data,
  onKots: (kots) => {
    const html = boardHtml(kots);
    if (html === drawn) return;
    drawn = html;
    grid.innerHTML = html;
  },
  onFailure: (message) => {
    drawn = null;
    grid.innerHTML = failedHtml(message);
  },
});

const actions = createKitchenActions({ api, ui, urls: data.urls, refresh: () => loader.refresh() });

const light = createKitchenLight();
light.show();

delegate(document.body, {
  "station.pick": (button) => {
    qsa(".station-btn").forEach((b) => b.classList.toggle("active", b === button));
    loader.setStation(button.dataset.station);
  },
  "item.start": (button) => actions.start(button.dataset.id),
  "item.ready": (button) => actions.ready(button.dataset.id),
  "kot.bump": (button) => actions.bump(button.dataset.id),
  "kitchen.clear": () => actions.clearAll(),
  "kitchen.light": () => light.toggle(),
});

onReady(() => {
  loader.refresh();
  setInterval(() => loader.refresh(), data.poll_ms || 5000);
});
