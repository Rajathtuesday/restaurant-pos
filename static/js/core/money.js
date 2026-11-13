/*
 * Money, written one way on every screen: whole rupees when there are no
 * paise ("₹430"), two decimals when there are ("₹20.50"), Indian grouping
 * ("₹1,25,000"), a minus before the sign ("-₹50"). core/money.py is the same
 * rule for the server; both are tested against one table of examples.
 */
function indian(whole) {
  if (whole.length <= 3) return whole;
  let head = whole.slice(0, -3);
  const tail = whole.slice(-3);
  const pairs = [];
  while (head.length > 2) {
    pairs.unshift(head.slice(-2));
    head = head.slice(0, -2);
  }
  if (head) pairs.unshift(head);
  return [...pairs, tail].join(",");
}

export function rupees(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "₹0";
  // Round half up on paise, as the server does (toFixed alone rounds 1.005 down).
  const paiseTotal = Math.round(Math.abs(n) * 100 + 1e-9);
  const whole = String(Math.floor(paiseTotal / 100));
  const paise = String(paiseTotal % 100).padStart(2, "0");
  const sign = n < 0 && paiseTotal > 0 ? "-" : "";
  return sign + "₹" + indian(whole) + (paise === "00" ? "" : "." + paise);
}
