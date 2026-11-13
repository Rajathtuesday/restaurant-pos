/*
 * Setup, table QR codes (setup/templates/setup/setup_qr_codes.html): every
 * table's code drawn on the page (core/qr.js), and Print All.
 */
import { drawQrCodes } from "../core/qr.js";
import { delegate } from "../core/dom.js";

drawQrCodes();

delegate(document.body, {
  "page.print": () => window.print(),
});
