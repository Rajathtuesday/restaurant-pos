"""
Who a rate limit counts, on the pages a guest reaches by QR code.

Counting by IP alone made every guest on one restaurant's Wi-Fi share one
allowance: three phones following their orders (each checks about 10 times a
minute) used up order_status's 30 a minute between them, and a busy evening
could lock a whole restaurant out of its own menu. A guest is now counted by
their IP and the token they came with (their table's or the counter's QR
token, or their order's status token), so each table gets its own allowance.
Staff placing orders are counted by their login.

Each view also keeps a looser cap per IP, so a caller making up tokens can't
buy a fresh allowance without end.
"""
import json

from core.utils import get_client_ip


def _guest(request, token):
    # The QR URLs hand the token over as a UUID (the <uuid:...> converter),
    # the JSON body as text: either way it is counted by its text.
    return f"{get_client_ip(request)}|{'' if token is None else token}"


def guest_by_url_token(group, request):
    """The guest's IP and the QR or order-status token in the URL."""
    kwargs = getattr(getattr(request, "resolver_match", None), "kwargs", None) or {}
    return _guest(request, kwargs.get("qr_token") or kwargs.get("signed_token"))


def guest_by_table_token(group, request):
    """The guest's IP and the ?table_token= of the menu page."""
    return _guest(request, request.GET.get("table_token"))


def order_placer(group, request):
    """create_order: staff by login, a guest by IP and the QR token they order with."""
    if request.user.is_authenticated:
        return f"user|{request.user.pk}"
    try:
        token = json.loads(request.body).get("table_token")
    except (ValueError, AttributeError):    # not JSON, or not a JSON object
        token = None
    return _guest(request, token)


def order_rate(group, request):
    """A cashier at a busy counter may place more orders a minute than a table."""
    return "60/m" if request.user.is_authenticated else "20/m"
