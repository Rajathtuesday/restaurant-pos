"""
Login lockouts: when a name is locked, when it opens, and what the login
page says.

django-axes counts wrong passwords (axes.models.AccessAttempt: one row per
name, address and browser, with how many failures and when the last one
was) and refuses a locked login before any password is checked. It can only
do that from FIRST place in AUTHENTICATION_BACKENDS (core/settings.py).

Two rules lock a name:

1. At one address: 5 wrong passwords for ravi from one address lock ravi
   at that address for an hour (AXES_LOCKOUT_PARAMETERS is
   [["username", "ip_address"]], AXES_FAILURE_LIMIT is 5). Axes does this.
   Kiran, on the same pub Wi-Fi, is not touched.
2. From anywhere: 20 wrong passwords for ravi within the hour, from any mix
   of addresses (a guesser hopping addresses). Axes 6.4 has one failure
   limit for every rule, so this rule is here, in RasovaAxesHandler: the
   place axes asks "is this locked?", for every authenticate() call.

What is never done: locking an address alone (every phone on a pub's Wi-Fi
shares one public address, so one waiter's typos would lock the whole pub
out), or locking a name alone at 5 (anyone could then lock any waiter out
from anywhere).

A lock opens an hour (AXES_COOLOFF_TIME) after the failure that made it. A
try while locked does not restart the hour. The owner or a manager opens it
at once with Unlock on Setup > Staff (or a password reset); an owner locked
out of their own account waits, or Rasova staff run
`manage.py axes_reset_username <name>`.
"""
from collections import defaultdict
from datetime import timedelta

from axes.handlers.database import AxesDatabaseHandler
from axes.helpers import get_client_ip_address, get_client_username, get_cool_off
from axes.models import AccessAttempt
from axes.signals import user_locked_out
from django.conf import settings
from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import render
from django.utils import dateformat, timezone

# Rule 2: wrong passwords for one name, from anywhere, within the hour.
NAME_ANYWHERE_LIMIT = 20


def at_address_limit():
    """Rule 1's count, from settings (AXES_FAILURE_LIMIT, 5 unless .env says)."""
    return settings.AXES_FAILURE_LIMIT


def failures_anywhere(username, at=None):
    """Wrong passwords for `username` in the hour before `at`, every address."""
    at = at or timezone.now()
    found = AccessAttempt.objects.filter(
        username=username, attempt_time__gte=at - get_cool_off(),
    ).aggregate(n=Sum("failures_since_start"))["n"]
    return found or 0


class RasovaAxesHandler(AxesDatabaseHandler):
    """Axes' own database handler (rule 1), plus rule 2: a name from anywhere."""

    def is_locked(self, request, credentials=None):
        if super().is_locked(request, credentials):
            return True
        username = get_client_username(request, credentials)
        return failures_anywhere(username, request.axes_attempt_time) >= NAME_ANYWHERE_LIMIT

    def user_login_failed(self, sender, credentials, request=None, **kwargs):
        # Axes records the failure and flags rule 1. The failure that brings
        # the name to 20 is refused as locked too, the way axes treats its 5th.
        super().user_login_failed(sender, credentials, request=request, **kwargs)
        if request is None or getattr(request, "axes_locked_out", False):
            return
        username = get_client_username(request, credentials)
        if failures_anywhere(username, request.axes_attempt_time) >= NAME_ANYWHERE_LIMIT:
            request.axes_locked_out = True
            request.axes_credentials = credentials
            user_locked_out.send("axes", request=request, username=username, ip_address=request.axes_ip_address)


def _opens_at(rows, limit, cool_off):
    """When the lock these rows make opens, or None if they make none.

    `rows` are (last failure time, failures), all inside the hour. The newest
    stay longest, so the lock holds until the row that brings the newest ones
    up to the limit is an hour old."""
    total = 0
    for when, failures in sorted(rows, key=lambda row: row[0], reverse=True):
        total += failures
        if total >= limit:
            return when + cool_off
    return None


def _rows(usernames, now):
    return AccessAttempt.objects.filter(
        username__in=list(usernames), attempt_time__gte=now - get_cool_off(),
    ).values_list("username", "ip_address", "attempt_time", "failures_since_start")


def locked_until(username, ip_address, now=None):
    """When `username` can log in from `ip_address` again, or None if now."""
    now = now or timezone.now()
    cool_off = get_cool_off()
    anywhere, here = [], []
    for _name, ip, when, failures in _rows([username], now):
        anywhere.append((when, failures))
        if ip == ip_address:
            here.append((when, failures))
    opens = [t for t in (
        _opens_at(here, at_address_limit(), cool_off),
        _opens_at(anywhere, NAME_ANYWHERE_LIMIT, cool_off),
    ) if t]
    return max(opens) if opens else None


def locks_for(usernames, now=None):
    """{username: when its last lock opens} for each of `usernames` locked
    anywhere right now, by either rule. One query, for the Staff page."""
    now = now or timezone.now()
    cool_off = get_cool_off()
    anywhere, at_address = defaultdict(list), defaultdict(list)
    for name, ip, when, failures in _rows(usernames, now):
        anywhere[name].append((when, failures))
        at_address[(name, ip)].append((when, failures))
    opens = {}
    found = [(name, _opens_at(rows, at_address_limit(), cool_off)) for (name, _ip), rows in at_address.items()]
    found += [(name, _opens_at(rows, NAME_ANYWHERE_LIMIT, cool_off)) for name, rows in anywhere.items()]
    for name, when in found:
        if when:
            opens[name] = max(opens.get(name, when), when)
    return opens


def unlock(username):
    """Forget every wrong password counted for `username`, at every address:
    both rules open at once. The password is not touched."""
    from axes.utils import reset
    return reset(username=username)


def clock(when):
    """'10:42 pm' in Rasova's time zone, rounded up to the minute, so a
    person who waits until the time shown is let in."""
    when = timezone.localtime(when)
    if when.second or when.microsecond:
        when = when.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return dateformat.format(when, "g:i A").lower()


def lock_message(until):
    if until is None:   # it opened in the moment since axes refused
        return "Too many wrong passwords for this name. Try again now, or ask the owner to unlock it."
    return f"Too many wrong passwords for this name. Try again after {clock(until)}, or ask the owner to unlock it."


def lockout_response(request, credentials=None):
    """Rasova's own login page, status 429, saying the name is locked and when
    it opens. It is axes' AXES_LOCKOUT_CALLABLE (axes' middleware asks for it
    whenever a request was refused as locked) and the login view's own answer;
    made once per request, so the message is added once."""
    made = getattr(request, "rasova_lockout_response", None)
    if made is not None:
        return made
    username = get_client_username(request, credentials)
    ip_address = getattr(request, "axes_ip_address", None) or get_client_ip_address(request)
    messages.error(request, lock_message(locked_until(username, ip_address)))
    made = render(request, "accounts/login.html", {"tenant": getattr(request, "tenant", None)}, status=429)
    request.rasova_lockout_response = made
    return made
