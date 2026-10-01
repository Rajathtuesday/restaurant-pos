import ipaddress
import logging
from datetime import timedelta, datetime, time
from functools import lru_cache

from django.conf import settings
from django.db.models import DateTimeField, ExpressionWrapper, F
from django.db.models.functions import TruncDate
from django.utils import timezone

logger = logging.getLogger("pos.core")


@lru_cache(maxsize=None)
def _networks(cidrs):
    return tuple(ipaddress.ip_network(c.strip(), strict=False) for c in cidrs if c.strip())


def _valid_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def ip_in(ip, cidrs):
    """True when `ip` is a valid address inside any of `cidrs` (plain IPs count as /32)."""
    try:
        address = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return False
    return any(address in network for network in _networks(tuple(cidrs)))


_warned_missing_real_ip = False


def get_client_ip(request):
    """
    The real visitor's IP behind Cloudflare -> nginx -> gunicorn.

    Every header a request carries can be written by whoever sends it, so a
    header is only believed when the machine that handed it over is one we
    trust to have set it:

    1. gunicorn listens on 127.0.0.1, so REMOTE_ADDR is nginx. nginx sets
       X-Real-IP to the address of whoever connected to it, replacing any
       X-Real-IP the client sent (`proxy_set_header`, nginx_rasova.conf).
       So X-Real-IP is believed only when REMOTE_ADDR is a trusted proxy.
    2. CF-Connecting-IP is believed only when that connecting address is
       one of Cloudflare's own (settings.CLOUDFLARE_IP_RANGES). Anyone who
       reaches the server directly, around Cloudflare, can send the header,
       and used to be taken at their word: any IP they liked, including
       127.0.0.1, the aggregator allowlist's default.
    3. X-Forwarded-For is never believed: nginx appends to whatever the
       client sent, so its first hop is the client's own claim.

    Rate limits, login lockouts (axes) and the aggregator allowlist all ask
    this one function.
    """
    global _warned_missing_real_ip
    meta = request.META
    peer = str(meta.get("REMOTE_ADDR", "") or "").strip()

    if ip_in(peer, settings.TRUSTED_PROXY_IPS):
        real_ip = str(meta.get("HTTP_X_REAL_IP", "") or "").strip()
        if _valid_ip(real_ip):
            peer = real_ip
        elif meta.get("HTTP_CF_CONNECTING_IP") and not _warned_missing_real_ip:
            # Cloudflare traffic arriving from nginx without X-Real-IP means
            # nginx is not configured as in nginx_rasova.conf: every visitor
            # would share one IP for rate limits and lockouts.
            _warned_missing_real_ip = True
            logger.warning(
                "Proxied request has CF-Connecting-IP but no X-Real-IP: "
                "check nginx sets `proxy_set_header X-Real-IP $remote_addr`."
            )

    if ip_in(peer, settings.CLOUDFLARE_IP_RANGES):
        visitor = str(meta.get("HTTP_CF_CONNECTING_IP", "") or "").strip()
        if _valid_ip(visitor):
            return visitor
    return peer


def _cutoff_hour(outlet=None):
    """The hour a business day starts: the outlet's, 6 AM by default."""
    if outlet and hasattr(outlet, 'business_day_start_hour'):
        return outlet.business_day_start_hour
    return 6


def get_business_date(dt=None, outlet=None):
    """
    Returns the business date for a given datetime based on the outlet's
    business day start hour. If no datetime is provided, uses current time.
    """
    if not dt:
        dt = timezone.now()
    
    # Convert to local time
    local_dt = timezone.localtime(dt)
    
    cutoff_hour = _cutoff_hour(outlet)
    if local_dt.hour < cutoff_hour:
        return local_dt.date() - timedelta(days=1)

    return local_dt.date()


def get_business_date_range(business_date, outlet=None):
    """
    Returns the (start, end) timezone-aware datetime bounds of a business
    day: from the outlet's cutoff hour on business_date to the same cutoff
    hour the next calendar day.

    Any "today's sales" style report that filters on a plain
    `created_at__date=` will silently misattribute orders placed after
    midnight but before the cutoff (e.g. 1 AM at a restaurant open past
    midnight) to the wrong business day — they'd count as "tomorrow" on a
    calendar-date filter, while get_business_date() correctly treats them
    as still belonging to the previous business day. Use this range with
    `created_at__gte=start, created_at__lt=end` to match that.
    """
    naive_start = datetime.combine(business_date, time(hour=_cutoff_hour(outlet)))
    current_tz = timezone.get_current_timezone()
    start = timezone.make_aware(naive_start, current_tz)
    end = start + timedelta(days=1)
    return start, end


def get_business_period(start_date, end_date, outlet=None):
    """The (start, end) bounds of the business days start_date to end_date,
    both included: from the cutoff hour on start_date to the cutoff hour after
    end_date. Filter with `created_at__gte=start, created_at__lt=end`, as for
    one day with get_business_date_range(). Every report that counts a day, a
    week or a month counts it this way, so they all agree on where a sale at
    1 AM belongs."""
    start, _ = get_business_date_range(start_date, outlet)
    _, end = get_business_date_range(end_date, outlet)
    return start, end


def business_date_of(field, outlet=None):
    """A database expression for the business day a timestamp falls in, to
    group rows by day: the local date once the clock is moved back by the
    cutoff hour, so a payment at 1 AM counts on the day still trading. (A
    plain TruncDate groups by the calendar day.)"""
    shifted = ExpressionWrapper(F(field) - timedelta(hours=_cutoff_hour(outlet)), output_field=DateTimeField())
    return TruncDate(shifted)
