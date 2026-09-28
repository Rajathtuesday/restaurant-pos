from datetime import timedelta, datetime, time

from django.db.models import DateTimeField, ExpressionWrapper, F
from django.db.models.functions import TruncDate
from django.utils import timezone


def get_client_ip(request):
    """
    Real client IP behind Cloudflare -> Nginx -> Gunicorn.

    Nginx forwards X-Real-IP/X-Forwarded-For correctly (nginx_rasova.conf),
    but Gunicorn only ever sees Nginx's own loopback connection, so
    request.META['REMOTE_ADDR'] is always 127.0.0.1 in production. Anything
    keying off REMOTE_ADDR directly (rate limits, lockouts) was silently
    treating every visitor as the same client.

    CF-Connecting-IP is set by Cloudflare itself and can't be spoofed by the
    client, so it's authoritative when present. Falls back to the first hop
    in X-Forwarded-For, then REMOTE_ADDR for direct/local connections
    (e.g. local dev, or hitting Nginx without Cloudflare in front).
    """
    cf_ip = request.META.get("HTTP_CF_CONNECTING_IP")
    if cf_ip:
        return cf_ip
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


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
