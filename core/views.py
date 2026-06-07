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


def demo_switch(request):
    """
    DEBUG-only tenant switcher for local demos.
    GET  /demo/          → shows tenant picker page
    GET  /demo/?as=slug  → sets session tenant + redirects to dashboard
    GET  /demo/?clear=1  → clears session tenant
    """
    if not settings.DEBUG:
        from django.http import Http404
        raise Http404

    from tenants.models import Tenant
    from django.shortcuts import render

    if request.GET.get("clear"):
        request.session.pop("dev_tenant_slug", None)
        return redirect("/demo/")

    pick = request.GET.get("as", "").strip().lower()
    if pick:
        try:
            t = Tenant.objects.get(slug__iexact=pick, is_active=True)
            request.session["dev_tenant_slug"] = t.slug
            return redirect("/dashboard/")
        except Tenant.DoesNotExist:
            pass

    tenants = Tenant.objects.filter(is_active=True).order_by("name")
    active_slug = request.session.get("dev_tenant_slug", "")
    return render(request, "core/demo_switch.html", {
        "tenants": tenants,
        "active_slug": active_slug,
    })


def health_check(request):
    try:
        connection.ensure_connection()
        return JsonResponse({"status": "healthy", "database": "connected"}, status=200)
    except Exception:
        # Public endpoint — never leak the raw DB error (may contain host/creds).
        # Log the detail server-side; return a generic status to the caller.
        import logging
        logging.getLogger("pos.core").exception("Health check DB connection failed")
        return JsonResponse({"status": "unhealthy", "database": "disconnected"}, status=503)
