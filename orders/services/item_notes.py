"""
A dish's note is what someone typed for the kitchen ("no onion"); its
modifiers (Peri Peri, Extra Cheese) are their own rows. The till used to
paste the chosen modifier names into the note as well ("Peri Peri" or
"Peri Peri | no onion"), so every KOT printed each modifier twice, once as
the note and once as the modifier (found on the walkthrough recording,
23 Nov 2026).

note_without_modifiers() takes that pasted list back off: the order service
stores notes through it, so a till still running old JavaScript (a cached
page, the offline queue) can't put it back, and OrderItem.typed_note reads
older rows through it.
"""


def note_without_modifiers(note, names):
    """The typed part of `note`, without a leading copy of the modifier
    names the till joins with ", " (and " | " before what was typed)."""
    note = (note or "").strip()
    names = [n for n in (names or []) if n]
    if not note or not names:
        return note
    listed = ", ".join(names)
    if note == listed:
        return ""
    if note.startswith(listed + " | "):
        return note[len(listed) + 3:].strip()
    return note
