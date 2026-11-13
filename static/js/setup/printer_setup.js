/*
 * The printer setup page (setup/templates/setup/printer_setup.html): its
 * Test Print button. The addresses come from page-data
 * (setup.views.core_views.printer_page_data).
 */
import { api } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
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

delegate(document.body, {
  "printer.test": async () => {
    button.disabled = true;
    try {
      await testPrint.run(document.getElementById("ipInput").value);
    } finally {
      button.disabled = false;
    }
  },
});
