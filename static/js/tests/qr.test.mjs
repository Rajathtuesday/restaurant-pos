/*
 * QR codes drawn on the page (static/js/core/qr.js), with a fake QR library.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { drawQrCodes } from "../core/qr.js";

function spot(data) {
  const classes = new Set();
  return {
    dataset: data, textContent: "", children: [],
    classList: { add: (c) => classes.add(c), has: (c) => classes.has(c) },
    replaceChildren(...kids) { this.children = kids; },
  };
}
const root = (spots) => ({ querySelectorAll: () => spots });
const canvas = () => { const attrs = {}; return { attrs, setAttribute: (k, v) => { attrs[k] = v; } }; };

test("each marked spot gets its code, at its size and colours, labelled", async () => {
  const asked = [];
  const QRCode = { toCanvas: (text, options, done) => { asked.push([text, options]); done(null, canvas()); } };
  const a = spot({ qr: "https://x.rasova.net/menu/t1/", qrSize: "158", qrLabel: "QR for T1" });
  const b = spot({ qr: "https://rasova.net/android/rasova.apk", qrSize: "90", qrDark: "#c5a059", qrLight: "#161616" });
  assert.deepEqual(await drawQrCodes(root([a, b]), QRCode), [true, true]);
  assert.equal(asked[0][0], "https://x.rasova.net/menu/t1/");
  assert.equal(asked[0][1].width, 158);
  assert.deepEqual(asked[1][1].color, { dark: "#c5a059", light: "#161616" });
  assert.equal(a.children[0].attrs["aria-label"], "QR for T1");
  assert.equal(b.children[0].attrs["aria-label"], "QR code");
});

test("no library, an error, or a throw says so in the spot", async () => {
  const none = spot({ qr: "x" });
  assert.deepEqual(await drawQrCodes(root([none]), undefined), [false]);
  assert.equal(none.textContent, "Could not draw this QR code.");
  assert.ok(none.classList.has("qr-failed"));
  const erred = spot({ qr: "x" });
  await drawQrCodes(root([erred]), { toCanvas: (t, o, done) => done(new Error("too long")) });
  assert.equal(erred.textContent, "Could not draw this QR code.");
  const threw = spot({ qr: "x" });
  await drawQrCodes(root([threw]), { toCanvas: () => { throw new Error("bad"); } });
  assert.equal(threw.textContent, "Could not draw this QR code.");
});
