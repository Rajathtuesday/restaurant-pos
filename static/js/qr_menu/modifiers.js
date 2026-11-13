/*
 * Picking a dish's options on the QR menu (Spice: Mild / Hot, Extras...).
 * Each group allows up to max_select; picking one more drops the oldest.
 * A required group must have a pick before the dish goes in the cart.
 */
export function createModifierPick(groups, basePrice) {
  const chosen = new Map();         // group id -> [{id, name, price}]

  const findGroup = (groupId) => groups.find((g) => String(g.id) === String(groupId));

  return {
    /* Pick or un-pick an option. Returns the ids now picked in its group. */
    toggle(groupId, modifierId) {
      const group = findGroup(groupId);
      if (!group) return [];
      const option = group.modifiers.find((m) => String(m.id) === String(modifierId));
      if (!option) return [];
      const picked = chosen.get(String(group.id)) || [];
      const at = picked.findIndex((m) => String(m.id) === String(option.id));
      if (at >= 0) {
        picked.splice(at, 1);
      } else {
        if (picked.length >= (Number(group.max_select) || 1)) picked.shift();
        picked.push({ id: option.id, name: option.name, price: Number(option.price) || 0 });
      }
      chosen.set(String(group.id), picked);
      return picked.map((m) => m.id);
    },
    isPicked(modifierId) {
      return Array.from(chosen.values()).some((picked) => picked.some((m) => String(m.id) === String(modifierId)));
    },
    total() {
      return (Number(basePrice) || 0) + this.modifiers().reduce((sum, m) => sum + m.price, 0);
    },
    /* The first required group with nothing picked, or null. */
    missing() {
      const group = groups.find((g) => g.is_required && !(chosen.get(String(g.id)) || []).length);
      return group ? group.name : null;
    },
    modifiers: () => Array.from(chosen.values()).flat(),
  };
}
