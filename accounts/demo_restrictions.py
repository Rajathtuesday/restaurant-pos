"""Guards for the public /live-demo/ trailer mode.

See md_files/rasova_demo_trailer_mode_plan_2026-09-07.html for the full
plan. A visitor who came in through the public magic link has
request.session["demo_trailer_mode"] set to True; the same account reached
via the founder key (?key=... on /live-demo/) has it set to False instead.
Checked against the demo_owner username too, as defense in depth -- this
should never matter for any other account, ever.
"""
from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

WHATSAPP_LINK = "https://wa.me/917899814912?text=Hi%2C%20I%27d%20like%20to%20see%20a%20demo%20of%20Rasova"
BLOCKED_MESSAGE = (
    "This is disabled in the live demo. Want to see it in action? "
    "Message us on WhatsApp."
)


def is_demo_trailer(request):
    if not request.session.get("demo_trailer_mode"):
        return False
    if not request.user.is_authenticated:
        return False
    from orders.scripts.demo_seed import DEMO_OWNER_USERNAME
    return request.user.username == DEMO_OWNER_USERNAME


def _safe_referer(request):
    referer = request.META.get("HTTP_REFERER", "")
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return referer
    return "/tables/"


def blocked_in_demo_trailer(view_func):
    """Wrap a view a trailer-mode visitor should never be able to complete.

    Detects whether the caller looks like a fetch()-based API call (this
    codebase's apiClient wrapper already turns {success: false, error: ...}
    into a toast automatically, see base.html) or a traditional form POST
    (whose templates render Django's own {% if messages %}), and answers
    in whichever shape that caller already knows how to handle. Either
    way, the account is left completely untouched.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if is_demo_trailer(request):
            wants_json = (
                request.headers.get("X-Requested-With") == "XMLHttpRequest"
                or request.content_type == "application/json"
                or "application/json" in request.headers.get("Accept", "")
            )
            if wants_json:
                return JsonResponse({"success": False, "error": BLOCKED_MESSAGE}, status=403)
            messages.info(request, f"{BLOCKED_MESSAGE} {WHATSAPP_LINK}")
            return redirect(_safe_referer(request))
        return view_func(request, *args, **kwargs)

    return wrapper
