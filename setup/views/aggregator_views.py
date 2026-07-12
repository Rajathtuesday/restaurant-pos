# setup/views/aggregator_views.py
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from core.decorators import tenant_required


# ==================================
# AGGREGATOR CONFIG
# ==================================

@login_required
@tenant_required
def aggregator_setup(request):
    if request.user.role != "owner":
        return redirect("setup_wizard")

    from setup.models import AggregatorConfig
    config, created = AggregatorConfig.for_outlet(request.user.outlet, request.user.tenant)

    if request.method == "POST":
        config.zomato_enabled = request.POST.get("zomato_enabled") == "on"
        config.swiggy_enabled = request.POST.get("swiggy_enabled") == "on"
        config.uber_eats_enabled = request.POST.get("uber_eats_enabled") == "on"
        config.auto_accept_orders = request.POST.get("auto_accept_orders") == "on"

        # Only overwrite a secret when a non-blank value is posted. This lets
        # the form render a masked/blank field without wiping the stored secret
        # on every save ("leave blank to keep"), matching how the Razorpay
        # secrets are handled in setup_payment_methods.
        zomato_secret = (request.POST.get("zomato_webhook_secret") or "").strip()
        swiggy_secret = (request.POST.get("swiggy_webhook_secret") or "").strip()
        if zomato_secret:
            config.zomato_webhook_secret = zomato_secret
        if swiggy_secret:
            config.swiggy_webhook_secret = swiggy_secret
        config.save()

        messages.success(request, "Aggregator configuration saved.")
        return redirect("setup_aggregators")

    webhook_url = request.build_absolute_uri(f"/orders/api/aggregator/webhook/?tenant_id={request.user.tenant.id}&outlet_id={request.user.outlet.id}")

    return render(request, "setup/aggregator_config.html", {
        "config": config,
        "webhook_url": webhook_url,
        "tenant_id": request.user.tenant.id,
        "outlet_id": request.user.outlet.id
    })


# ==================================
# AGGREGATOR QUICK TOGGLE
# One-tap on/off for online orders — called from token dashboard
# and owner dashboard without navigating to full settings.
# ==================================

@login_required
@tenant_required
@require_POST
def toggle_aggregator(request):
    """
    Quick toggle for online order platforms.
    Body: { "platform": "zomato" | "swiggy" | "all", "enabled": true | false }
    Role: manager or owner only.
    """
    if request.user.role not in ["owner", "manager"]:
        return JsonResponse({"error": "Permission denied"}, status=403)

    from setup.models import AggregatorConfig
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, Exception):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    platform = data.get("platform", "").strip().lower()
    enabled  = bool(data.get("enabled", False))

    if platform not in ("zomato", "swiggy", "all"):
        return JsonResponse({"error": "Invalid platform. Use zomato, swiggy, or all."}, status=400)

    config, _ = AggregatorConfig.for_outlet(request.user.outlet, request.user.tenant)

    if platform == "all":
        config.zomato_enabled  = enabled
        config.swiggy_enabled  = enabled
        config.save(update_fields=["zomato_enabled", "swiggy_enabled"])
    elif platform == "zomato":
        config.zomato_enabled = enabled
        config.save(update_fields=["zomato_enabled"])
    elif platform == "swiggy":
        config.swiggy_enabled = enabled
        config.save(update_fields=["swiggy_enabled"])

    logging.getLogger("pos.orders").info(
        "Aggregator toggle | outlet=%s | platform=%s | enabled=%s | user=%s",
        request.user.outlet.id, platform, enabled, request.user.username
    )

    return JsonResponse({
        "success":        True,
        "zomato_enabled": config.zomato_enabled,
        "swiggy_enabled": config.swiggy_enabled,
    })
