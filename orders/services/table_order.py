"""
Tables in the order a person counts them: T1, T2 ... T9, T10, not T1, T10,
T11, T2 (the database sorts names as text). A pub with 14 tables saw its
floor plan run T1, T10, T11, T12, T13, T14, T2 (the walkthrough recording,
23 Nov 2026). Every list of tables goes through by_name().
"""
import re

_PARTS = re.compile(r"(\d+)")


def natural_key(name):
    """"T10" -> ("t", 10, ""): text compared without case, numbers as numbers."""
    return tuple(int(part) if part.isdigit() else part.casefold() for part in _PARTS.split(str(name or "")))


def by_name(tables, name=lambda t: t["name"] if isinstance(t, dict) else t.name):
    """Tables (models, or dicts with a "name") sorted the way people count."""
    return sorted(tables, key=lambda t: natural_key(name(t)))
