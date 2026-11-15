"""
One name, one dish, per outlet (15 Nov 2026).

A KOT, a bill and the guest QR menu show a dish by its name alone, so two
dishes called "Uta" at one outlet (Rs 200 and Rs 170) leave the cashier
and the kitchen guessing which was meant. The Add Item form accepted the
second one; the imports checked only an exact match in the same category,
so "Uta " or "UTA", or the same dish under another category, slipped in.

Names compare without case and with runs of spaces counted as one. Every
place that adds or renames a dish asks here: the Add Item and Edit forms
(refused, with the existing dish named), the AI menu import and the
onboarding steps (a dish already on the menu is left as it is).
"""
from menu.models import MenuItem


def clean(name):
    """The name as it is saved: trimmed, inner spaces single."""
    return " ".join((name or "").split())


def same_name(tenant, outlet, name, exclude_id=None):
    """The dish at this outlet already called `name`, or None."""
    wanted = clean(name).casefold()
    if not wanted:
        return None
    first_word = wanted.split(" ")[0]
    candidates = MenuItem.objects.filter(tenant=tenant, outlet=outlet, name__icontains=first_word)
    if exclude_id is not None:
        candidates = candidates.exclude(id=exclude_id)
    for dish in candidates.select_related("category"):
        if clean(dish.name).casefold() == wanted:
            return dish
    return None


def taken_message(dish):
    where = f" in {dish.category.name}" if dish.category_id else ""
    return (f"There is already a dish called \"{dish.name}\"{where}. "
            "Use another name, or edit that dish.")


def add_unless_named(tenant, outlet, category, name, defaults):
    """For the imports: (dish, created). A dish with this name already at the
    outlet is returned as it is, not duplicated."""
    existing = same_name(tenant, outlet, name)
    if existing is not None:
        return existing, False
    return MenuItem.objects.create(tenant=tenant, outlet=outlet, category=category, name=clean(name), **defaults), True
