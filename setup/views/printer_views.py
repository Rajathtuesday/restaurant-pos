"""
Setup > Printers: every printer of the outlet on one page (22 Nov 2026).

Before, a printer's settings were spread over four places: Kitchen Stations
(each printer's address, paper, cut, code page, and a Test Print that only
worked with a PC agent on the same device), Setup > Printer (the counter
printer's address again, its paper again, its MAC, and a Test Print through
the queue), Outlet Settings (a summary, and a save that wiped the MAC, the
PC agent's address and QZ Tray on every press) and the Django admin (KOT big
letters, totals on the KOT, the bill's margins), which no owner can reach.

Now: one row per printer with its live state from the print queue and a
Test Print that goes through the queue, so it works the same on a box (the
box prints it) and in the cloud (the phone app or PC agent collects it);
and one form for what applies to every printer. Kitchen Stations keeps
what is about stations (which dishes go where, the default, "made as soon
as sent") and links here for the printer.
"""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.demo_restrictions import BLOCKED_MESSAGE, is_demo_trailer
from box.edition import is_box
from core.decorators import role_required, tenant_required
from setup.models import KitchenStation
from setup.services import printers as svc


def _station(request, station_id):
    return get_object_or_404(KitchenStation, id=station_id, tenant=request.user.tenant, outlet=request.user.outlet)


def page_data(outlet):
    """What static/js/setup/printers.js reads; 0 stands in for an id."""
    return {
        "box": is_box(),
        "agentHost": (outlet.agent_host or "localhost") if outlet else "localhost",
        "urls": {
            "save": reverse("printer_save", args=[0]),
            "test": reverse("printer_test", args=[0]),
            "job_status": reverse("print-job-status", args=[0]),
        },
    }


@login_required
@tenant_required
@role_required("owner", "manager")
def printers(request):
    outlet = request.user.outlet
    if outlet is None:
        # A platform superuser passes both decorators with no outlet; printers
        # belong to an outlet, so there is nothing to show (a 500 before).
        messages.error(request, "Printers belong to an outlet: sign in as the restaurant's owner or manager.")
        return redirect("/dashboard/")
    if request.method == "POST":
        if is_demo_trailer(request):
            messages.info(request, BLOCKED_MESSAGE)
            return redirect("setup_printers")
        error = _save_every_printer(request, outlet)
        (messages.error if error else messages.success)(request, error or "Saved for every printer.")
        return redirect("setup_printers")

    stations = svc.outlet_stations(outlet)
    states = svc.printer_states(outlet, stations)
    profile = outlet.print_profile
    return render(request, "setup/printers.html", {
        "outlet": outlet,
        "rows": [{"station": s, "state": states[s.id]} for s in stations],
        "kot_large_font": profile.kot_large_font if profile else True,
        "kot_show_total": profile.kot_show_total if profile else True,
        "bill_inner_margin": profile.bill_inner_margin if profile else 4,
        "margins": svc.MARGINS,
        "is_box": is_box(),
        "page_data": page_data(outlet),
    })


def _save_every_printer(request, outlet):
    """KOT big letters, totals on the KOT and the bill's side margin, for
    every printer of this outlet; in the cloud also the PC agent's address
    and QZ Tray. Returns an error sentence, or None."""
    post = request.POST
    try:
        margin = int(post.get("bill_inner_margin", "4"))
    except ValueError:
        margin = -1
    if margin not in svc.MARGINS:
        return "Choose a bill margin from the list."
    profile = _own_profile(outlet)
    profile.kot_large_font = post.get("kot_large_font") == "on"
    profile.kot_show_total = post.get("kot_show_total") == "on"
    profile.bill_inner_margin = margin
    profile.save(update_fields=["kot_large_font", "kot_show_total", "bill_inner_margin"])
    if not is_box() and "agent_host" in post:
        host = post.get("agent_host", "").strip() or "localhost"
        if len(host) > 100 or any(c.isspace() for c in host):
            return "The PC agent's address is one word, like 192.168.1.20 or localhost."
        outlet.agent_host = host
        outlet.use_qz_tray = post.get("use_qz_tray") == "on"
        outlet.save(update_fields=["agent_host", "use_qz_tray"])
    return None


def _own_profile(outlet):
    """The outlet's print profile, made if it has none. A profile shared
    with another outlet (set up in the admin) is copied first, so changing
    one outlet's KOTs here never changes another outlet's."""
    from tenants.models import Outlet, PrintProfile

    profile = outlet.print_profile
    if profile and not Outlet.objects.filter(print_profile=profile).exclude(pk=outlet.pk).exists():
        return profile
    base = (outlet.name or "Outlet")[:90]
    name, n = base, 2
    while PrintProfile.objects.filter(tenant_id=outlet.tenant_id, name=name).exists():
        name, n = f"{base} {n}", n + 1
    fields = {"kot_large_font": True, "kot_show_total": True, "bill_inner_margin": 4}
    if profile:
        fields = {k: getattr(profile, k) for k in fields}
    profile = PrintProfile.objects.create(tenant_id=outlet.tenant_id, name=name, **fields)
    outlet.print_profile = profile
    outlet.save(update_fields=["print_profile"])
    return profile


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def printer_save(request, station_id):
    station = _station(request, station_id)
    if is_demo_trailer(request):
        return JsonResponse({"error": BLOCKED_MESSAGE}, status=403)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return JsonResponse({"error": "Send the printer's settings."}, status=400)
    try:
        svc.save_printer(station, body)
    except svc.PrinterInputError as e:
        return JsonResponse({"error": str(e)}, status=400)
    state = svc.printer_states(request.user.outlet, [station])[station.id]
    return JsonResponse({"success": True, "message": f"Saved {station.name}'s printer.", "state": state["words"],
                         "level": state["level"], "printer_ip": station.printer_ip or ""})


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def printer_test(request, station_id):
    return _queue_test(_station(request, station_id))


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def printer_test_print(request):
    """The counter printer's test page (the address the old Setup > Printer
    page used; kept for anything still posting to it)."""
    from setup.services.station_service import get_default_station
    return _queue_test(get_default_station(request.user))


def _queue_test(station):
    if not station.printer_ip:
        return JsonResponse({"error": f"Set {station.name}'s printer address and Save first, then test."}, status=422)
    job = svc.queue_test_print(station)
    return JsonResponse({"success": True, "job_id": job.pk, "message": svc.test_print_message()})


@login_required
def printer_setup(request):
    """The old Setup > Printer page lives on Setup > Printers now."""
    return redirect("setup_printers", permanent=True)
