"""The box's own pages: the first switch-on, and its health."""
import os
import shutil

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import connection, transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from core.decorators import role_required, tenant_required

SETUP_LOCK = 727702


def _box_only():
    from box.edition import is_box
    if not is_box():
        raise Http404("Only on a Rasova box.")


def setup(request):
    """The first switch-on: name the restaurant and make the owner's login.
    Only while no restaurant exists; after that it is gone for good."""
    _box_only()
    from tenants.models import Tenant
    if Tenant.objects.exists():
        return redirect("/login/")
    from tenants.services.restaurant_setup import RestaurantSetupError, create_restaurant
    types = sorted(Tenant.TenantType.choices, key=lambda c: c[0] != "pub")     # pub first
    error = None
    if request.method == "POST":
        if request.POST.get("password") != request.POST.get("password_again"):
            error = "The two passwords don't match."
        elif len(request.POST.get("password") or "") < 8:
            error = "Use a password of at least 8 characters: this login runs the whole restaurant."
        else:
            try:
                with transaction.atomic():
                    # Two people on two phones pressing "Start" at once make one restaurant.
                    with connection.cursor() as cur:
                        cur.execute("SELECT pg_advisory_xact_lock(%s)", [SETUP_LOCK])
                    if Tenant.objects.exists():
                        return redirect("/login/")
                    tenant, outlet, owner = create_restaurant(
                        name=request.POST.get("name"), tenant_type=request.POST.get("tenant_type"),
                        outlet_name=request.POST.get("outlet_name"), gst_no=request.POST.get("gst_no"),
                        owner_username=request.POST.get("username"), owner_password=request.POST.get("password"),
                    )
                    from box.models import BoxState
                    BoxState.get()
                    BoxState.objects.filter(pk=1).update(setup_done_at=timezone.now(), last_seen_at=timezone.now())
            except RestaurantSetupError as e:
                error = e.message
            else:
                cache.set("box_setup_done", True, None)
                login(request, owner, backend="django.contrib.auth.backends.ModelBackend")
                return redirect("/setup/onboard/")
    return render(request, "box/setup.html", {"types": types, "error": error, "posted": request.POST})


@login_required
@tenant_required
@role_required("owner", "manager")
def health(request):
    _box_only()
    from box import backup, clock, license
    from box.models import BoxState
    from setup.models import KitchenStation

    is_owner = request.user.role == "owner"
    if request.method == "POST":
        if not is_owner:
            messages.error(request, "Only the owner can change this.")
            return redirect("box_health")
        action = request.POST.get("action")
        if action == "licence" and request.FILES.get("licence"):
            raw = request.FILES["licence"].read(20000).decode("utf-8", "replace")
            state = license.install(raw)
            (messages.success if not state.blocks_new_orders else messages.error)(request, state.message)
        elif action == "clock":
            clock.clear_warning()
            messages.success(request, "Clock warning cleared. The box starts counting from the time it shows now.")
        elif action == "backup":
            try:
                path = backup.run_backup()
                messages.success(request, f"Backup made: {path.name}.")
            except backup.BackupError as e:
                messages.error(request, f"Backup failed: {e}")
        cache.delete("box_warnings")
        return redirect("box_health")

    state = BoxState.get()
    folder = backup.backup_dir()
    try:
        usage = shutil.disk_usage(str(folder) if folder.exists() else "/")
        disk = {"free_gb": round(usage.free / 1024 ** 3, 1), "total_gb": round(usage.total / 1024 ** 3, 1),
                "free_pct": round(usage.free / usage.total * 100)}
    except OSError:
        disk = None
    return render(request, "box/health.html", {
        "same_drive": _same_drive(folder, license.license_path().parent),
        "version": getattr(settings, "RASOVA_VERSION", "dev"),
        "box_id": license.machine_id(),
        "licence": license.current(refresh=True),
        "state": state,
        "now": timezone.localtime(),
        "backups": backup.list_backups()[:14],
        "disk": disk,
        "stations": KitchenStation.objects.filter(tenant=request.user.tenant, outlet=request.user.outlet),
        "is_owner": is_owner,
    })


def _same_drive(backups, data):
    """True when the backups sit on the same drive as the box's own data (the
    backup stick isn't there, or was never set up): one dead disk would take
    both. Unknown counts as not the same: no warning without a reason."""
    try:
        return os.stat(backups).st_dev == os.stat(data).st_dev
    except OSError:
        return False


def media(request, path):
    """Menu photos and logos from the box's own disk. A box has no cloud
    storage (and no nginx in front); the cloud serves media from R2, so
    this is a 404 there."""
    if not getattr(settings, "BOX", False):
        raise Http404
    from django.views.static import serve
    return serve(request, path, document_root=settings.MEDIA_ROOT)
