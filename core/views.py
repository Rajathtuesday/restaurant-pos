import os
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.db import connection
from django.shortcuts import redirect

logger = logging.getLogger("pos.core")


def landing(request):
    if request.user.is_authenticated:
        return redirect("/dashboard/")
    # Serve the marketing page as raw HTML — bypasses the Django template
    # engine so the pure-HTML file never trips on characters like { or %.
    import os
    from django.conf import settings
    path = os.path.join(settings.BASE_DIR, "core", "templates", "core", "landing.html")
    try:
        with open(path, "rb") as f:
            return HttpResponse(f.read(), content_type="text/html; charset=utf-8")
    except FileNotFoundError:
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
