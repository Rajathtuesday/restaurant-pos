/*
 * Setup, table QR codes (setup/templates/setup/setup_qr_codes.html): every
 * table's code drawn on the page (core/qr.js). Print All is the shared
 * "page.print" (core/page_actions.js, handled by core/app.js).
 */
import { drawQrCodes } from "../core/qr.js";

drawQrCodes();
