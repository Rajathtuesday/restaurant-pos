"""
The box's clock. Bill numbers, the business day, liquor hours and happy
hours all read it, and a box has no one watching it.

The box remembers the latest time it has seen (BoxState.last_seen_at). If
the clock is ever more than BACKWARDS_SLACK behind that, it went back (a
dead clock battery after a power cut, someone changing the date): a warning
shows on every page until the owner fixes the time and clears it. A jump
of more than FORWARD_JUMP ahead is flagged the same way. Nothing is ever
blocked because of the clock: refusing bills at 9 pm would be worse.
The box sets its own time from the internet whenever it has one (the
installer turns on time sync).
"""
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

BACKWARDS_SLACK = timedelta(minutes=5)
FORWARD_JUMP = timedelta(days=45)
OBSERVE_EVERY_SECONDS = 60


def observe(now=None, force=False):
    """Note the time; at most once a minute unless forced."""
    if not force and cache.get("box_clock_seen"):
        return
    cache.set("box_clock_seen", True, OBSERVE_EVERY_SECONDS)
    from box.models import BoxState
    now = now or timezone.now()
    with transaction.atomic():
        state = BoxState.get(lock=True)
        last = state.last_seen_at
        fields = []
        if last is not None and now < last - BACKWARDS_SLACK:
            state.clock_warning = (f"The box's clock went back: it says {timezone.localtime(now):%d %b %Y %H:%M}, "
                                   f"but it already saw {timezone.localtime(last):%d %b %Y %H:%M}. Set the right time.")
            state.clock_warning_at = now
            fields += ["clock_warning", "clock_warning_at"]
        elif last is not None and now > last + FORWARD_JUMP:
            state.clock_warning = (f"The box's clock jumped ahead to {timezone.localtime(now):%d %b %Y}. "
                                   "If that's not today, set the right time.")
            state.clock_warning_at = now
            fields += ["clock_warning", "clock_warning_at"]
        if last is None or now > last:
            state.last_seen_at = now
            fields.append("last_seen_at")
        if fields:
            state.save(update_fields=fields)


def clear_warning(now=None):
    """The owner has fixed the time: forget the warning and start again from now."""
    from box.models import BoxState
    with transaction.atomic():
        state = BoxState.get(lock=True)
        state.clock_warning, state.clock_warning_at = "", None
        state.last_seen_at = now or timezone.now()
        state.save(update_fields=["clock_warning", "clock_warning_at", "last_seen_at"])
