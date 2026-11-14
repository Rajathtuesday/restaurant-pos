/*
 * A printer's MAC address as it is typed: hex digits only, upper case, a
 * colon after every two, at most 12 digits ("0057271" -> "00:57:27:1").
 */
export function formatMac(typed) {
  const hex = String(typed || "").replace(/[^0-9a-fA-F]/g, "").toUpperCase().slice(0, 12);
  return hex.replace(/(..)(?=.)/g, "$1:");
}
