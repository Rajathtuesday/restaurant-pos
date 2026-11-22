/*
 * Box > Connect a device (box/templates/box/connect.html): draws the QR
 * code that pairs a phone with this box. Drawn here from Rasova's own QR
 * library: a box has no internet, so nothing may come from outside.
 */
import { drawQrCodes } from "../core/qr.js";

drawQrCodes();
