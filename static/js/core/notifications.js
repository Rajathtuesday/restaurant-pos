/*
 * The notification poll: every 8 seconds, /api/notifications/ says how
 * many waiter calls, kitchen messages, QR orders awaiting approval and
 * stock alerts there are. New ones get a toast (and a beep for a QR order,
 * a vibration on a phone, a browser notification when allowed); the badges
 * on the header icons show the counts.
 */
const URL = "/api/notifications/";

/* A short two-tone beep through the Web Audio API: nothing to ship. Browsers
   allow sound only after the page was touched, so the very first alert on a
   page nobody has clicked yet is silent; the toast and the badge still show. */
function beep() {
  try {
    const Ctx = globalThis.AudioContext || globalThis.webkitAudioContext;
    const ctx = new Ctx();
    [880, 1175].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.15, ctx.currentTime + i * 0.15);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.15 + 0.14);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + i * 0.15);
      osc.stop(ctx.currentTime + i * 0.15 + 0.15);
    });
  } catch (noAudio) {
    /* the badge and the toast still work */
  }
}

function browserNotification(title, body) {
  const N = globalThis.Notification;
  if (!N) return;
  if (N.permission === "granted") {
    new N(title, { body, icon: "/static/icons/icon-192.png" });
  } else if (N.permission !== "denied") {
    N.requestPermission(); // asked once; never nagged
  }
}

export function createNotificationPoller({
  fetchFn = (...args) => globalThis.fetch(...args),
  ui,
  doc = document,
  nav = globalThis.navigator,
  interval = 8000,
} = {}) {
  // -1 means "not seen yet": the first poll sets the badges without toasting
  // everything that was already there.
  let prevWaiter = -1;
  let prevKitchen = -1;
  let prevQr = -1;
  let prevSystem = -1;
  let timer = null;

  function badge(baseId, count) {
    for (const id of [baseId, baseId + "-mobile"]) {
      const el = doc.getElementById(id);
      if (!el) continue;
      if (count > 0) {
        el.textContent = count > 99 ? "99+" : String(count);
        el.classList.remove("hidden");
      } else {
        el.classList.add("hidden");
      }
    }
  }

  const vibrate = (pattern) => {
    if (nav && nav.vibrate) nav.vibrate(pattern);
  };

  async function poll() {
    // Never crash the POS over a notification: a failed poll is skipped.
    try {
      const res = await fetchFn(URL, { headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (!res.ok) return;
      const data = await res.json();

      const waiter = data.waiter_calls.count || 0;
      const kitchen = data.kitchen_messages.count || 0;
      badge("notif-badge-waiter", waiter + kitchen);

      if (prevWaiter !== -1 && waiter > prevWaiter) {
        const fresh = data.waiter_calls.items.slice(0, waiter - prevWaiter);
        fresh.forEach((call) => ui.toast(`🔔 Waiter called from ${call.table}`, "warning"));
        vibrate([200, 100, 200]);
        browserNotification(`Waiter Call: ${fresh[0]?.table || "Table"}`, "Customer is calling for assistance.");
      }
      prevWaiter = waiter;

      if (prevKitchen !== -1 && kitchen > prevKitchen) {
        const fresh = data.kitchen_messages.items.slice(0, kitchen - prevKitchen);
        fresh.forEach((msg) => ui.toast(`Kitchen (${msg.table}): ${msg.message}`, "info"));
        vibrate([100, 50, 100]);
        browserNotification(`Kitchen Alert: ${fresh[0]?.table || "Table"}`, fresh[0]?.message || "Message from kitchen");
      }
      prevKitchen = kitchen;

      if (data.qr_orders) {
        const qr = data.qr_orders.count || 0;
        badge("notif-badge-qr", qr);
        if (prevQr !== -1 && qr > prevQr) {
          const fresh = data.qr_orders.items.slice(0, qr - prevQr);
          fresh.forEach((o) => ui.toast(`🧺 New QR order: ${o.table}. Tap Tables to approve.`, "success"));
          vibrate([200, 100, 200, 100, 200]);
          beep();
          browserNotification(
            `New QR Order: ${fresh[0]?.table || "Table"}`,
            "A customer placed an order from the table QR menu. Approve it to send to the kitchen.",
          );
        }
        prevQr = qr;
      }

      // Low stock and other system alerts: the badge exists for owners and
      // managers only, so this is a no-op for everyone else.
      if (data.notifications) {
        const system = data.notifications.length || 0;
        badge("notif-badge-system", system);
        if (prevSystem !== -1 && system > prevSystem) {
          data.notifications.slice(0, system - prevSystem).forEach((n) => ui.toast(`⚠️ ${n.message}`, "warning"));
        }
        prevSystem = system;
      }
    } catch (blip) {
      /* the next poll recovers */
    }
  }

  return {
    poll,
    start() {
      poll();
      timer = setInterval(poll, interval);
    },
    stop() {
      if (timer) clearInterval(timer);
      timer = null;
    },
  };
}
