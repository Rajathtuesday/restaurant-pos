# inventory/unit_conversion.py
"""
Unit conversion between the small set of units Rasova tracks inventory in.

Recipe, ModifierRecipe, RequisitionItem and BatchItem each store their own
`unit`, independent of the InventoryItem they reference. Nothing used to
convert between them — a recipe entered in grams against an item tracked in
kilograms was silently treated as a raw number, off by a factor of 1000.
This module is the single place that conversion happens, so every stock
deduction/addition path uses the same rules instead of each re-deriving them.
"""
from decimal import Decimal

# Which measurement family each unit belongs to. Conversion is only valid
# within the same family — grams can become kilograms, but grams can never
# become pieces or millilitres; those are fundamentally different quantities.
UNIT_FAMILY = {
    "g":   "weight",
    "kg":  "weight",
    "ml":  "volume",
    "l":   "volume",
    "pcs": "count",
}

# Conversion factor from each unit to its family's canonical base unit
# (grams for weight, millilitres for volume, pieces for count).
_TO_BASE = {
    "g":   Decimal("1"),
    "kg":  Decimal("1000"),
    "ml":  Decimal("1"),
    "l":   Decimal("1000"),
    "pcs": Decimal("1"),
}


class IncompatibleUnitsError(ValueError):
    """Raised when asked to convert between two units in different measurement
    families (e.g. grams to pieces) — there is no correct number to return,
    so callers must handle this explicitly rather than get a silently wrong one."""


def convert_quantity(quantity, from_unit: str, to_unit: str) -> Decimal:
    """
    Convert `quantity` expressed in `from_unit` into the equivalent amount in
    `to_unit`. Returns a Decimal.

    Raises IncompatibleUnitsError if the two units are not in the same
    measurement family, or if either unit is unrecognized — callers should
    catch this, log it loudly, and skip that line rather than deduct/add a
    meaningless number.
    """
    quantity = Decimal(str(quantity))
    if from_unit == to_unit:
        return quantity

    fam_from = UNIT_FAMILY.get(from_unit)
    fam_to = UNIT_FAMILY.get(to_unit)
    if fam_from is None:
        raise IncompatibleUnitsError(f"Unknown unit: {from_unit!r}")
    if fam_to is None:
        raise IncompatibleUnitsError(f"Unknown unit: {to_unit!r}")
    if fam_from != fam_to:
        raise IncompatibleUnitsError(
            f"Cannot convert {from_unit!r} ({fam_from}) to {to_unit!r} ({fam_to}) "
            f"— they measure different things."
        )

    base_qty = quantity * _TO_BASE[from_unit]
    return base_qty / _TO_BASE[to_unit]


def units_compatible(unit_a: str, unit_b: str) -> bool:
    """True if the two units are either equal or in the same measurement family."""
    if unit_a == unit_b:
        return True
    fam_a = UNIT_FAMILY.get(unit_a)
    fam_b = UNIT_FAMILY.get(unit_b)
    return fam_a is not None and fam_a == fam_b
