/*
 * A name for one order request, so the server can tell "the same request
 * again" from "a new one".
 *
 * An order placed with no internet is kept on the device and sent when
 * the connection is back. If the server took it and the answer was lost
 * on the way, the device cannot know, and sends it again. The request
 * carries an id (request_id); the server remembers the ids it has taken
 * (orders.OrderRequest) and answers a repeat with the first order.
 */

function hex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/* A random UUID. crypto.randomUUID needs https (or localhost); the
   fallbacks are for an old WebView and for plain http on a LAN. */
export function newRequestId(cryptoObj = globalThis.crypto) {
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") return cryptoObj.randomUUID();
  const bytes = new Uint8Array(16);
  if (cryptoObj && typeof cryptoObj.getRandomValues === "function") {
    cryptoObj.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;      // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80;      // RFC 4122 variant
  const h = hex(bytes);
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/*
 * The id for what a screen is about to send.
 *
 *   const ids = createRequestIds();
 *   const body = ids.stamp(payload);     // payload plus request_id
 *   ...the server answered (yes or no), or the order was queued:
 *   ids.settle();
 *
 * The same payload stamped again before settle() gets the same id: that
 * is the cashier tapping again after "Network error", when the first try
 * may or may not have arrived. A changed cart is a different request and
 * gets a new id. After settle() the next stamp is always new, so the same
 * two dishes for the next customer are a new order.
 */
export function createRequestIds({ newId = newRequestId } = {}) {
  let sent = null;
  let id = null;
  return {
    stamp(payload) {
      const text = JSON.stringify(payload);
      if (text !== sent) {
        sent = text;
        id = newId();
      }
      return { ...payload, request_id: id };
    },
    settle() {
      sent = null;
      id = null;
    },
  };
}
