/*
 * The sums on the bill page that happen before anything is sent: the
 * change to give back, a split share, and the text inside the UPI QR.
 * Pure: no DOM, no network. The server decides what is actually owed.
 */

/* Rupees to hand back when the guest gives more than the balance, else 0. */
export function changeDue(entered, balance) {
  const change = Math.round((Number(entered) || 0) - (Number(balance) || 0));
  return change > 0 ? change : 0;
}

/* One person's share of the balance, to the paisa, or null if the number
   of people is not a whole number from 1 up. */
export function splitShare(balance, people) {
  const n = Number(people);
  if (!Number.isInteger(n) || n < 1) return null;
  return (Number(balance) / n).toFixed(2);
}

/* What a UPI app reads from the QR: who is paid, how much, for which bill.
   The payee's name is encoded: "Salt & Pepper" used to cut the link at the
   ampersand, so the app saw a payee called "Salt " and lost the amount's
   neighbours. The UPI id itself goes as it is (name@bank). */
export function upiString({ upiId, payee, amount, billNumber }) {
  return "upi://pay?pa=" + upiId
    + "&pn=" + encodeURIComponent(payee)
    + "&am=" + amount
    + "&cu=INR&tn=Bill%20" + encodeURIComponent(billNumber);
}

/* The amount field's text as a number the server will take, or null. */
export function payable(text) {
  const amount = Number(String(text).trim());
  return String(text).trim() !== "" && Number.isFinite(amount) && amount >= 0 ? amount : null;
}
