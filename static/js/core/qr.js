/*
 * QR codes drawn on the page, by the QR library Rasova serves itself
 * (static/js/vendor/qrcode.min.js, a classic script that sets window.QRCode).
 *
 * Two setup pages used to fetch their QR codes as images from
 * api.qrserver.com: every table's private menu address was handed to a
 * third party, and the codes were blank with no internet. Now a page marks
 * where a code goes and this draws it:
 *
 *   <div class="qr-draw" data-qr="https://..." data-qr-size="180"></div>
 *
 * Optional: data-qr-dark / data-qr-light (colours), data-qr-label (alt text).
 */
export function drawQrCodes(root = document, QRCode = globalThis.QRCode) {
  const spots = Array.from(root.querySelectorAll("[data-qr]"));
  return Promise.all(spots.map((spot) => new Promise((resolve) => {
    const failed = () => {
      spot.textContent = "Could not draw this QR code.";
      spot.classList.add("qr-failed");
      resolve(false);
    };
    if (!QRCode || typeof QRCode.toCanvas !== "function") {
      failed();
      return;
    }
    const size = Number(spot.dataset.qrSize) || 180;
    try {
      QRCode.toCanvas(spot.dataset.qr, {
        width: size, margin: 1,
        color: { dark: spot.dataset.qrDark || "#000000", light: spot.dataset.qrLight || "#ffffff" },
      }, (err, canvas) => {
        if (err || !canvas) {
          failed();
          return;
        }
        canvas.setAttribute("role", "img");
        canvas.setAttribute("aria-label", spot.dataset.qrLabel || "QR code");
        spot.replaceChildren(canvas);
        resolve(true);
      });
    } catch (broken) {
      failed();
    }
  })));
}
