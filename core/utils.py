from datetime import timedelta
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


def get_business_date(dt=None, outlet=None):
    """
    Returns the business date for a given datetime based on the outlet's
    business day start hour. If no datetime is provided, uses current time.
    """
    if not dt:
        dt = timezone.now()
    
    # Convert to local time
    local_dt = timezone.localtime(dt)
    
    # Get cutoff from outlet, default to 6 AM
    cutoff_hour = 6
    if outlet and hasattr(outlet, 'business_day_start_hour'):
        cutoff_hour = outlet.business_day_start_hour
        
    if local_dt.hour < cutoff_hour:
        return local_dt.date() - timedelta(days=1)
    
    return local_dt.date()
