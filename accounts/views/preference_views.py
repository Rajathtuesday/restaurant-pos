"""
A staff member's own screen preferences.

header_style_view: how the header's buttons look for this person (Fit the
screen, icons and words, icons only, words only). The header always fits on
one row (static/js/core/header_fit.js); this only changes what each button
shows. Saved on the account, so it follows them to any device. Freeze lifted
by the owner for this one setting on 21 Nov 2026 (CLAUDE.md).
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from accounts.models import User


@login_required
@require_POST
def header_style_view(request):
    try:
        style = str(json.loads(request.body or b"{}").get("style", ""))
    except (ValueError, AttributeError):
        style = ""
    if style not in dict(User.HEADER_STYLES):
        return JsonResponse({"error": "Pick one of: " + ", ".join(dict(User.HEADER_STYLES).values()) + "."}, status=400)
    User.objects.filter(pk=request.user.pk).update(header_style=style)
    return JsonResponse({"success": True, "style": style})
