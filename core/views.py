import os
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.db import connection
from django.shortcuts import redirect

logger = logging.getLogger("pos.core")


def landing(request):
    # WhiteNoise serves public/index.html at / for unauthenticated users
    # before this view is ever called. This view only runs when WhiteNoise
    # doesn't intercept — e.g. authenticated users who should go to dashboard.
    if request.user.is_authenticated:
        return redirect("/dashboard/")
    return redirect("/login/")


def serve_sw(request):
    sw_path = os.path.join(settings.BASE_DIR, "static", "sw.js")
    try:
        with open(sw_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = "// service worker not found"
    response = HttpResponse(content, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


def health_check(request):
    try:
        connection.ensure_connection()
        return JsonResponse({"status": "healthy", "database": "connected"}, status=200)
    except Exception as e:
        return JsonResponse({"status": "unhealthy", "database": "disconnected", "error": str(e)}, status=503)
