/*
 * Purchase orders: the sums and checks behind the New / Edit drawer and the
 * Receive drawer, with no page in sight so node can test them.
 *
 * A drawer line is {item_id, quantity, unit_price} as typed (strings).
 */

/* The order's total from what is typed; a blank or bad number counts 0. */
export function poTotal(lines) {
  return lines.reduce((sum, l) => sum + (parseFloat(l.quantity) || 0) * (parseFloat(l.unit_price) || 0), 0);
}

/* {items} to send, or {problem} in words. A line left wholly blank is
   skipped; a line half filled is a problem. */
export function poItems(lines) {
  const items = [];
  for (const l of lines) {
    const filled = [l.item_id, l.quantity, l.unit_price].filter((v) => v !== "" && v != null).length;
    if (filled === 0) continue;
    if (filled < 3) return { problem: "Please fill all fields for each item row." };
    items.push({ item_id: parseInt(l.item_id, 10), quantity: parseFloat(l.quantity), unit_price: parseFloat(l.unit_price) });
  }
  if (!items.length) return { problem: "Add at least one item." };
  return { items };
}

/* The lines still to receive: [{item_id, name, unit, remaining, price}]. */
export function toReceive(lines) {
  return (lines || [])
    .map((l) => ({ ...l, remaining: Number(l.quantity) - Number(l.quantity_received) }))
    .filter((l) => l.remaining > 0)
    .map((l) => ({ item_id: l.item_id, name: l.name, unit: l.unit, remaining: l.remaining, price: Number(l.ordered_price) }));
}

/* What the receive view reads: {item id: {quantity_received, invoiced_price}}. */
export function receiveBody(rows) {
  const items = {};
  for (const r of rows) {
    items[r.item_id] = {
      quantity_received: r.quantity === "" ? "0" : r.quantity,
      invoiced_price: r.price === "" ? null : r.price,
    };
  }
  return { items };
}

export function receivedWords(status) {
  return status === "partially_received" ? "Partially received, stock updated." : "PO fully received, stock updated.";
}
