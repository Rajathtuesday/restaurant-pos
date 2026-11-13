/*
 * The guest's cart on the QR menu: what they picked, how many, what it
 * comes to, roughly how long it takes, and the cart as /create-order/ reads
 * it. State only: the page draws it (view.js).
 */
export function createGuestCart() {
  const items = new Map();          // dish id (text) -> line, in the order they were added

  const unit = (line) => line.price + (line.modifierPrice || 0);

  return {
    /* A dish, with what was chosen for it. The same dish again adds one. */
    add(dish, modifiers = []) {
      const id = String(dish.id);
      const line = items.get(id);
      if (line) {
        line.qty += 1;
        return line;
      }
      const added = {
        id, name: dish.name, price: Number(dish.price) || 0, qty: 1,
        prepTime: Number(dish.prepTime) || 15, gstRate: Number(dish.gstRate) || 0,
        modifiers, modifierPrice: modifiers.reduce((sum, m) => sum + (Number(m.price) || 0), 0),
      };
      items.set(id, added);
      return added;
    },
    inc(id) {
      const line = items.get(String(id));
      if (line) line.qty += 1;
      return line ? line.qty : 0;
    },
    /* One fewer; the line goes at 0. Returns what is left of it. */
    dec(id) {
      const line = items.get(String(id));
      if (!line) return 0;
      line.qty -= 1;
      if (line.qty <= 0) items.delete(String(id));
      return Math.max(0, line.qty);
    },
    clear() {
      items.clear();
    },
    has: (id) => items.has(String(id)),
    qty: (id) => (items.get(String(id)) || { qty: 0 }).qty,
    lines: () => Array.from(items.values()),
    count: () => Array.from(items.values()).reduce((n, l) => n + l.qty, 0),
    total: () => Array.from(items.values()).reduce((sum, l) => sum + l.qty * unit(l), 0),
    lineTotal: (line) => line.qty * unit(line),

    /* "12 to 20 min": the slowest dish, since the kitchen cooks in parallel. */
    eta() {
      if (!items.size) return null;
      const slowest = Math.max(...Array.from(items.values()).map((l) => l.prepTime || 15));
      return { low: Math.max(5, slowest - 3), high: slowest + 5 };
    },

    payload: () => Array.from(items.values()).map((l) => ({
      id: Number(l.id) || l.id, quantity: l.qty, price: l.price, modifiers: (l.modifiers || []).map((m) => m.id),
    })),
  };
}
