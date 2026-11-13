/*
 * The Razorpay QR sheet: ask the server for a QR for the amount in the
 * payment field (so a split share is what gets charged, not the whole
 * balance), show it, and ask every 3 seconds whether it was paid, until
 * it is paid, it expires, or the sheet is closed.
 *
 * The states are big on purpose: a waiter glancing for one second must
 * know "paid" or "not paid" without reading.
 */
export function createRazorpayQr({
  api, urls, ui, doc = document, getAmount, onPaid,
  now = () => Date.now(), every = (fn, ms) => setInterval(fn, ms), stop = (id) => clearInterval(id),
  later = (fn, ms) => setTimeout(fn, ms),
}) {
  let timer = null;
  const el = (id) => doc.getElementById(id);

  function stopAsking() {
    if (timer !== null) stop(timer);
    timer = null;
  }

  function show(state, statusText) {
    const card = el("razorpay-modal-card");
    card.classList.toggle("is-paid", state === "paid");
    if (statusText !== undefined) el("razorpay-qr-status").textContent = statusText;
  }

  function area(html) {
    el("razorpay-qr-area").innerHTML = html;
  }

  async function ask(qrCodeId, expiresAt) {
    if (now() > expiresAt) {
      stopAsking();
      show("waiting", "QR expired. Tap Regenerate.");
      return;
    }
    let s;
    try {
      s = await api.get(urls.razorpay_qr_status.replace("PLACEHOLDER", encodeURIComponent(qrCodeId)));
    } catch (blip) {
      return;                      // keep asking: one failed request must not stop it
    }
    if (s.status === "paid") {
      stopAsking();
      area('<div class="razorpay-paid-mark"><span>✓</span></div>');
      show("paid", "PAID ✓");
      ui.toast("Payment received", "success");
      later(onPaid, 2000);         // held long enough to be seen
    } else if (s.status === "expired") {
      stopAsking();
      show("waiting", "QR expired. Tap Regenerate.");
    }
  }

  return {
    async open() {
      stopAsking();
      show("waiting", "Waiting for payment…");
      el("razorpay-modal-overlay").classList.add("is-open");
      area('<span class="razorpay-note">Generating QR…</span>');
      let data;
      try {
        data = await api.post(urls.razorpay_create_qr, { amount: getAmount() });
      } catch (shown) {
        area('<span class="razorpay-note razorpay-note--error">Could not generate QR</span>');
        show("waiting", "Tap Regenerate to try again");
        return;
      }
      const img = doc.createElement("img");
      img.src = data.image_url;
      img.alt = "Razorpay UPI QR";
      img.className = "razorpay-qr-image";
      const box = el("razorpay-qr-area");
      box.innerHTML = "";
      box.appendChild(img);
      const expiresAt = new Date(data.expires_at).getTime();
      timer = every(() => ask(data.qr_code_id, expiresAt), 3000);
    },

    close() {
      el("razorpay-modal-overlay").classList.remove("is-open");
      stopAsking();
    },

    get asking() {
      return timer !== null;
    },
  };
}
