/*
 * The printer setup page (setup/templates/setup/printer_setup.html): its
 * Test Print button, the paper width cards, the Advanced (port) switch and
 * the MAC address field's formatting. The addresses come from page-data
 * (setup.views.core_views.printer_page_data).
 */
import { api } from "../core/app.js";
import { delegate, qsa } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { drawQrCodes } from "../core/qr.js";
import { formatMac } from "./mac.js";
import { createTestPrint } from "./test_print.js";

const data = pageData("page-data");
const button = document.getElementById("testBtn");
const result = document.getElementById("testResult");

const testPrint = createTestPrint({
  api,
  fetchFn: (...args) => fetch(...args),
  urls: data.urls,
  show: (level, words) => {
    result.className = "test-result result-line is-" + level;
    result.textContent = words;
  },
});

// The app download and this restaurant's login, drawn here: no outside host.
drawQrCodes();

delegate(document.body, {
  "printer.test": async () => {
    button.disabled = true;
    try {
      await testPrint.run(document.getElementById("ipInput").value);
    } finally {
      button.disabled = false;
    }
  },
  "printer.width": (card) => {
    qsa(".width-card").forEach((c) => {
      c.classList.toggle("selected", c === card);
      c.setAttribute("aria-pressed", c === card ? "true" : "false");
    });
    document.getElementById("h_width").value = card.dataset.width;
  },
  "printer.advanced": (toggle) => {
    const open = document.getElementById("advRow").classList.toggle("show");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  },
});

delegate(document.body, {
  "mac.format": (input) => { input.value = formatMac(input.value); },
}, "input");
