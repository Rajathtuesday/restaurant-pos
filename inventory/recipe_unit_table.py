# inventory/recipe_unit_table.py
"""
Parses the free-text quantity phrases Gemini extracts from a recipe
("2 cups", "a pinch", "1/2 tsp", "200g") into Rasova's native unit set
(g/kg/ml/l/pcs) — or explicitly refuses to guess when the phrase is genuinely
ambiguous.

This is deliberately separate from inventory/unit_conversion.py: that module
handles conversion BETWEEN two already-known native units (g<->kg, ml<->l).
This module's job is turning colloquial recipe language into a native unit in
the first place. Nothing here ever approximates ingredient density (e.g. "a
cup of flour" to grams) — cup/tbsp/tsp are well-defined VOLUME measures and
convert to ml; anything that would require guessing a weight from a volume
measure (or vice versa) is left for a human to enter, same as a phrase this
parser doesn't recognize at all.
"""
import re
from decimal import Decimal, InvalidOperation

from inventory.models import UNIT_CHOICES

_NATIVE_UNITS = {code for code, _ in UNIT_CHOICES}

# Well-defined volume measures with an unambiguous standard mL value.
# Picks the US customary standard (documented here, not silently assumed) —
# a reviewer can always override in the review UI if a different regional
# standard applies.
COLLOQUIAL_VOLUME_TO_ML = {
    "cup": Decimal("240"), "cups": Decimal("240"),
    "tbsp": Decimal("15"), "tablespoon": Decimal("15"), "tablespoons": Decimal("15"),
    "tsp": Decimal("5"), "teaspoon": Decimal("5"), "teaspoons": Decimal("5"),
}

# Exact synonyms for a piece-count — not an approximation, just other words
# for the same thing.
_PCS_SYNONYMS = {"pcs", "pc", "piece", "pieces", "nos", "no", "number", "numbers"}

# Phrases with no numeric quantity at all — never guessed.
AMBIGUOUS_PHRASES = {
    "to taste", "a pinch", "a dash", "a handful", "as needed", "as required",
    "as desired", "for garnish", "for serving", "some", "a few", "a little",
}

# ½ etc. — the common ones that actually show up in recipes.
_UNICODE_FRACTIONS = {
    "½": Decimal("0.5"), "¼": Decimal("0.25"), "¾": Decimal("0.75"),
    "⅓": Decimal("1") / Decimal("3"), "⅔": Decimal("2") / Decimal("3"),
    "⅛": Decimal("0.125"), "⅜": Decimal("0.375"), "⅝": Decimal("0.625"), "⅞": Decimal("0.875"),
}

# "1 1/2", "1/2", "2", "2.5" — a leading number, optionally a unicode
# fraction, optionally a plain fraction, followed by an optional unit word.
_QTY_RE = re.compile(
    r"^\s*(?P<whole>\d+(?:\.\d+)?)?\s*"
    r"(?P<unicode_frac>[½¼¾⅓⅔⅛⅜⅝⅞])?\s*"
    r"(?:(?P<frac_num>\d+)\s*/\s*(?P<frac_den>\d+))?\s*"
    r"(?P<unit>[a-zA-Z]+)?"
)

# A bare number range ("200-250", "1-2") — never averaged or guessed.
_RANGE_RE = re.compile(r"^\s*\d+(?:\.\d+)?\s*[-–—]\s*\d+(?:\.\d+)?\s*[a-zA-Z]*\s*$")


def parse_quantity_text(raw_text: str):
    """
    Returns (quantity: Decimal|None, unit: str|None, needs_manual: bool).

    quantity/unit are only ever set together, in one of Rasova's native units
    (g/kg/ml/l/pcs). needs_manual=True means: don't guess, show this row
    amber in the review UI, and require the reviewer to type a quantity.
    """
    if not raw_text or not raw_text.strip():
        return None, None, True

    text = raw_text.strip().lower()

    # Ambiguous phrases — check as a substring so "salt to taste" and "to
    # taste" both match, not just an exact whole-string match.
    if any(phrase in text for phrase in AMBIGUOUS_PHRASES):
        return None, None, True

    # A range ("200-250g") — no safe single number to use.
    if _RANGE_RE.match(text):
        return None, None, True

    match = _QTY_RE.match(text)
    if not match:
        return None, None, True

    quantity = _resolve_numeric(match)
    if quantity is None or quantity <= 0:
        return None, None, True

    unit_word = (match.group("unit") or "").strip().lower()
    if not unit_word:
        # A bare number with no unit at all ("2 onions" already stripped the
        # ingredient name upstream, so this shouldn't normally happen — but
        # if it does, there's nothing safe to assume).
        return None, None, True

    if unit_word in _NATIVE_UNITS:
        return quantity, unit_word, False

    if unit_word in _PCS_SYNONYMS:
        return quantity, "pcs", False

    if unit_word in COLLOQUIAL_VOLUME_TO_ML:
        return quantity * COLLOQUIAL_VOLUME_TO_ML[unit_word], "ml", False

    # Unrecognized unit word ("medium", "clove", "slice", "inch"...) — could
    # silently be wrong in either direction, so refuse rather than guess.
    return None, None, True


def _resolve_numeric(match) -> Decimal | None:
    """Combine whole + unicode-fraction + plain-fraction groups into one
    Decimal, e.g. "1 1/2" -> 1.5, "½" -> 0.5, "2" -> 2."""
    total = Decimal("0")
    found_any = False

    whole = match.group("whole")
    if whole:
        try:
            total += Decimal(whole)
            found_any = True
        except InvalidOperation:
            return None

    unicode_frac = match.group("unicode_frac")
    if unicode_frac:
        total += _UNICODE_FRACTIONS[unicode_frac]
        found_any = True

    frac_num, frac_den = match.group("frac_num"), match.group("frac_den")
    if frac_num and frac_den:
        try:
            denom = Decimal(frac_den)
            if denom == 0:
                return None
            total += Decimal(frac_num) / denom
            found_any = True
        except InvalidOperation:
            return None

    return total if found_any else None
