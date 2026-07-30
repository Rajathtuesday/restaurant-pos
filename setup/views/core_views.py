# setup/views/core_views.py
import json
import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from orders.models import Table
from menu.models import MenuCategory, MenuItem
from tenants.models import Outlet
from setup.models import KitchenStation, PaymentConfig
from core.decorators import tenant_required, role_required
from accounts.models import User

logger = logging.getLogger("pos.setup")


# -------------------------------------------------
# MAIN SETUP DASHBOARD
# -------------------------------------------------

@login_required
def setup_wizard(request):

    if request.user.role not in ["owner", "manager"]:
        return redirect("/dashboard/")

    tenant = request.user.tenant
    outlet = request.user.outlet

    if not tenant or not outlet:
        messages.error(request, "User is not assigned to a tenant/outlet.")
        return redirect("/dashboard/")

    if request.method == "POST":
        if "logo" in request.FILES:
            tenant.logo = request.FILES["logo"]
            tenant.save(update_fields=["logo"])
            messages.success(request, "Restaurant logo updated successfully.")
            return redirect("setup_wizard")

    tables_exist = Table.objects.filter(
        tenant=tenant,
        outlet=outlet
    ).exists()

    categories_exist = MenuCategory.objects.filter(
        tenant=tenant,
        outlet=outlet
    ).exists()

    items_exist = MenuItem.objects.filter(
        tenant=tenant,
        outlet=outlet
    ).exists()

    context = {
        "tables_done": tables_exist,
        "menu_done": categories_exist and items_exist
    }

    return render(request, "setup/setup.html", context)


# -------------------------------------------------
# TABLE CREATION
# -------------------------------------------------

@login_required
@transaction.atomic
def setup_tables(request):

    if request.user.role not in ["owner", "manager"]:
        return redirect("/dashboard/")

    tenant = request.user.tenant
    outlet = request.user.outlet

    tables = Table.objects.filter(
        tenant=tenant,
        outlet=outlet
    ).order_by("name")

    if request.method == "POST":

        try:
            count = int(request.POST.get("table_count", 0))
        except (ValueError, TypeError):
            messages.error(request, "Invalid table count")
            return redirect("setup_tables")

        if count <= 0 or count > 200:

            messages.error(request, "Table count must be between 1 and 200")

            return redirect("setup_tables")

        existing_count = tables.count()

        new_tables = []

        for i in range(1, count + 1):

            number = existing_count + i

            new_tables.append(

                Table(
                    tenant=tenant,
                    outlet=outlet,
                    name=f"Table {number}"
                )

            )

        Table.objects.bulk_create(new_tables)

        messages.success(request, f"{count} tables added successfully")

        return redirect("setup_tables")

    return render(
        request,
        "setup/setup_tables.html",
        {"tables": tables}
    )

# -------------------------------------------------
# MENU SETUP
# -------------------------------------------------

@login_required
@transaction.atomic
def setup_menu(request):

    if request.user.role not in ["owner", "manager"]:
        return redirect("/dashboard/")

    tenant = request.user.tenant
    outlet = request.user.outlet

    categories = MenuCategory.objects.filter(
        tenant=tenant,
        outlet=outlet
    ).order_by("display_order")

    if request.method == "POST":

        name = request.POST.get("category_name")

        if not name:
            messages.error(request, "Category name required")
            return redirect("setup_menu")

        # prevent duplicates
        if MenuCategory.objects.filter(
            tenant=tenant,
            outlet=outlet,
            name=name
        ).exists():

            messages.warning(request, "Category already exists")
            return redirect("setup_menu")

        MenuCategory.objects.create(
            tenant=tenant,
            outlet=outlet,
            name=name
        )

        messages.success(request, "Category created")

        return redirect("setup_menu")

    return render(
        request,
        "setup/setup_menu.html",
        {
            "categories": categories
        }
    )


# -------------------------------------------------
# KITCHEN STATIONS SETUP
# -------------------------------------------------

@login_required
def setup_kitchen_stations(request):

    if request.user.role not in ["owner", "manager"]:
        return redirect("/dashboard/")

    stations = KitchenStation.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    if request.method == "POST":

        name = request.POST.get("station_name")

        if not name:
            messages.error(request, "Station name required")
            return redirect("/setup/kitchen-stations/")  # ✅ FIX

        if KitchenStation.objects.filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            name=name.strip()
            ).exists():
            messages.warning(request, "Station already exists")
            return redirect("/setup/kitchen-stations/")

        is_first = not KitchenStation.objects.filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
        ).exists()
        KitchenStation.objects.create(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            name=name.strip(),
            is_default=is_first,
        )

        messages.success(request, "Kitchen station created")

        return redirect("/setup/kitchen-stations/")  # ✅ FIX

    stations_list = list(stations.order_by("is_default", "name").reverse())

    # Determine print mode for the strip preview.
    # Only non-default stations matter: if any have their own printer IP,
    # they print KOTs independently (per-station mode).
    # The default station always has the cashier printer — don't count it.
    default_station = next((s for s in stations_list if s.is_default), None) or (stations_list[0] if stations_list else None)
    any_station_has_printer = any(
        s.printer_ip for s in stations_list
        if not s.is_default
    )
    cashier_ip = default_station.printer_ip if default_station else None

    return render(
        request,
        "setup/setup_kitchen_stations.html",
        {
            "stations": stations_list,
            "any_station_has_printer": any_station_has_printer,
            "cashier_ip": cashier_ip,
            "default_station": default_station,
            "outlet": request.user.outlet,
        }
    )

# -------------------------------------------------
# PRINTER CONFIG — update IP/port/paper/cut/encoding for a station
# -------------------------------------------------

@login_required
@role_required("owner", "manager")
@require_POST
def update_printer_config(request, station_id):
    station = get_object_or_404(
        KitchenStation,
        id=station_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet,
    )
    # apiClient sends JSON body — request.POST is empty for JSON requests
    import json as _json
    try:
        body = _json.loads(request.body)
    except Exception:
        body = request.POST

    ip   = (body.get("printer_ip") or "").strip() or None
    port = str(body.get("printer_port") or "9100").strip()
    paper = str(body.get("paper_width_mm") or "80").strip()
    cut  = (body.get("cut_type") or "full").strip()
    enc  = (body.get("printer_encoding") or "cp437").strip()

    station.printer_ip = ip
    station.printer_port = int(port) if port.isdigit() else 9100
    station.paper_width_mm = int(paper) if paper in ("58", "80") else 80
    station.cut_type = cut if cut in ("full", "partial", "none") else "full"
    station.printer_encoding = enc if enc in ("cp437", "cp850", "utf-8") else "cp437"
    # QZ Tray printer name (Windows printer name for USB printing)
    pname = (body.get("printer_name") or "").strip()
    if pname is not None:
        station.printer_name = pname
    station.save(update_fields=["printer_ip", "printer_port", "paper_width_mm", "cut_type", "printer_encoding", "printer_name"])

    return JsonResponse({"success": True, "message": f"Printer settings saved for {station.name}"})


@login_required
@role_required("owner", "manager")
@require_POST
def test_print_station(request, station_id):
    station = get_object_or_404(
        KitchenStation,
        id=station_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet,
    )
    if not station.printer_ip:
        return JsonResponse({"error": "No printer IP set for this station."}, status=400)

    from orders.services.printing_service import PrintingService
    svc = PrintingService(
        printer_type="network",
        host=station.printer_ip,
        port=station.printer_port,
        chars_per_line=station.chars_per_line,
        cut_type=station.cut_type,
        encoding=station.printer_encoding,
    )
    success = svc.test_print(station_name=station.name)
    if success:
        return JsonResponse({"success": True, "message": "Test page sent to printer"})
    return JsonResponse({"error": "Could not connect to printer. Check the IP and port, and make sure the printer is on."}, status=500)


# -------------------------------------------------
# PAYMENT METHODS
# -------------------------------------------------

@login_required
def setup_payment_methods(request):
    """
    Persists payment method configuration per outlet to the database.
    Replaces the old session-based approach.

    Owner/manager only: this view writes the tenant's Razorpay credentials
    and UPI VPA. Without a role gate, any authenticated staff account
    (default role is cashier) could swap in attacker-owned payment
    credentials and silently redirect every card/UPI transaction.
    """
    if request.user.role not in ["owner", "manager"]:
        return redirect("/dashboard/")

    tenant = request.user.tenant
    outlet = request.user.outlet

    # Get or create the config record for this outlet
    config, _ = PaymentConfig.for_outlet(outlet, tenant)

    if request.method == "POST":
        config.cash_enabled = "cash" in request.POST.getlist("methods")
        config.upi_enabled = "upi" in request.POST.getlist("methods")
        config.card_enabled = "card" in request.POST.getlist("methods")

        # Optional label overrides
        cash_label = request.POST.get("cash_label", "").strip()
        upi_label = request.POST.get("upi_label", "").strip()
        card_label = request.POST.get("card_label", "").strip()
        if cash_label:
            config.cash_label = cash_label
        if upi_label:
            config.upi_label = upi_label
        if card_label:
            config.card_label = card_label

        config.upi_id = request.POST.get("upi_id", "").strip().lower()

        from core.features import has_feature
        if has_feature(tenant, "razorpay_gateway"):
            config.razorpay_enabled = request.POST.get("razorpay_enabled") == "on"
            razorpay_key_id = request.POST.get("razorpay_key_id", "").strip()
            razorpay_key_secret = request.POST.get("razorpay_key_secret", "").strip()
            razorpay_webhook_secret = request.POST.get("razorpay_webhook_secret", "").strip()
            if razorpay_key_id:
                config.razorpay_key_id = razorpay_key_id
            if razorpay_key_secret:
                config.razorpay_key_secret = razorpay_key_secret
            if razorpay_webhook_secret:
                config.razorpay_webhook_secret = razorpay_webhook_secret

        config.save()
        messages.success(request, "Payment methods saved.")
        return redirect("/dashboard/")

    from core.features import has_feature
    from django.urls import reverse
    razorpay_feature_enabled = has_feature(tenant, "razorpay_gateway")
    razorpay_webhook_url = None
    if razorpay_feature_enabled:
        razorpay_webhook_url = request.build_absolute_uri(
            f"{reverse('razorpay-webhook')}?tenant_id={tenant.id}&outlet_id={outlet.id}"
        )

    return render(request, "setup/setup_payment_methods.html", {
        "config": config,
        "razorpay_feature_enabled": razorpay_feature_enabled,
        "razorpay_webhook_url": razorpay_webhook_url,
    })


@login_required
def setup_staff(request):

    if request.user.role not in ["owner", "manager"]:
        return redirect("/dashboard/")

    tenant = request.user.tenant
    outlet = request.user.outlet

    # select_related("outlet") -- setup_staff.html renders member.outlet.name
    # per row; without it, that's one extra query per staff member.
    staff = User.objects.filter(
        tenant=tenant
    ).exclude(role="owner").select_related("outlet")

    outlets = Outlet.objects.filter(tenant=tenant)

    if request.method == "POST":

        username = request.POST.get("username")
        password = request.POST.get("password")
        role = request.POST.get("role")

        if not username or not password:

            messages.error(request, "Username and password required")

            return redirect("setup_staff")

        # Never trust the posted role. Without this, a manager (or any
        # owner/manager) could create a brand-new "owner" account and
        # escalate privilege through a form that looks access-controlled.
        if role not in User.ASSIGNABLE_STAFF_ROLES:
            messages.error(request, "Invalid role selected.")
            return redirect("setup_staff")

        if User.objects.filter(username=username).exists():

            messages.error(request, "Username already exists")

            return redirect("setup_staff")

        outlet_id = request.POST.get("outlet")
        selected_outlet = None
        if outlet_id:
            try:
                selected_outlet = Outlet.objects.get(id=outlet_id, tenant=tenant)
            except Outlet.DoesNotExist:
                messages.error(request, "Invalid outlet selected")
                return redirect("setup_staff")

        user = User.objects.create_user(
            username=username,
            password=password,
            role=role,
            tenant=tenant,
            outlet=selected_outlet
        )

        messages.success(request, f"Staff account '{username}' created ({role})")

        return redirect("setup_staff")

    # Authoritative role list for the edit-role modal — built from
    # ASSIGNABLE_STAFF_ROLES rather than copied from the create-account
    # dropdown above, so it can't drift from what's actually assignable.
    role_choices = [
        (value, label) for value, label in User.ROLE_CHOICES
        if value in User.ASSIGNABLE_STAFF_ROLES
    ]

    return render(
        request,
        "setup/setup_staff.html",
        {"staff": staff, "outlets": outlets, "role_choices": role_choices}
    )


@login_required
@role_required("owner", "manager")
@require_POST
def reset_staff_password(request, user_id):
    """
    Owner/manager resets a staff member's password directly, without needing
    server/SSH access. Also clears any axes lockout on that account, since a
    forgotten password and a lockout from repeated wrong guesses usually
    happen together and both need clearing for the person to actually get
    back in.
    """
    # Scoped to this tenant and excludes role="owner", matching the same
    # boundary the staff list itself already uses — a manager (or another
    # owner from this same tenant-scoped list) can't reach an account
    # outside their tenant by guessing an id, and can't touch an owner
    # account through this endpoint.
    target = get_object_or_404(
        User, id=user_id, tenant=request.user.tenant
    )
    if target.role == "owner":
        return JsonResponse({"error": "Owner passwords can't be reset here."}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request."}, status=400)

    new_password = (data.get("password") or "").strip()
    if len(new_password) < 8:
        return JsonResponse({"error": "Password must be at least 8 characters."}, status=400)

    target.set_password(new_password)
    target.save(update_fields=["password"])

    from axes.utils import reset as axes_reset
    axes_reset(username=target.username)

    return JsonResponse({"success": True, "message": f"Password reset for {target.username}."})


@login_required
@role_required("owner", "manager")
@require_POST
def toggle_staff_active(request, user_id):
    """
    Deactivate/reactivate a staff account, rather than deleting it.

    A hard delete would cascade into real business history — shift
    schedules (StaffSchedule.staff) and cash sessions
    (CashSession.opened_by) both CASCADE on the user FK, and several
    other tables (orders, inventory transfers, requisitions) reference
    the user for audit purposes. Deactivating preserves every one of
    those records exactly as they were, blocks the account from
    logging in again (Django rejects is_active=False at authenticate()),
    and is reversible if it turns out to be a mistake.
    """
    target = get_object_or_404(
        User, id=user_id, tenant=request.user.tenant
    )
    if target.role == "owner":
        return JsonResponse({"error": "Owner accounts can't be deactivated here."}, status=403)

    target.is_active = not target.is_active
    target.save(update_fields=["is_active"])

    if not target.is_active:
        # is_active flipping to False does NOT invalidate a session that was
        # already established before this change — Django only checks
        # is_active during authenticate() at login time, not on every
        # subsequent request. Without this, a staff member deactivated
        # mid-shift could keep using the app until their session expired
        # on its own (default: two weeks).
        from django.contrib.sessions.models import Session
        for session in Session.objects.filter(expire_date__gte=timezone.now()):
            data = session.get_decoded()
            if str(data.get("_auth_user_id")) == str(target.id):
                session.delete()

    action = "deactivated" if not target.is_active else "reactivated"
    return JsonResponse({
        "success": True,
        "is_active": target.is_active,
        "message": f"{target.username} {action}.",
    })


@login_required
@role_required("owner", "manager")
@require_POST
def edit_staff_role(request, user_id):
    """
    Changes an existing staff member's role. Unlike is_active (only checked
    at login), role_required reads request.user.role fresh from the DB on
    every request, so the new role takes effect on their very next click —
    no forced logout or session invalidation needed.
    """
    target = get_object_or_404(
        User, id=user_id, tenant=request.user.tenant
    )
    if target.role == "owner":
        return JsonResponse({"error": "Owner's role can't be changed here."}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request."}, status=400)

    new_role = data.get("role")
    # Never trust the posted role — same boundary as staff creation above:
    # without this, a manager could post role="owner" and escalate someone
    # (or themselves, via a second account) straight to full ownership.
    if new_role not in User.ASSIGNABLE_STAFF_ROLES:
        return JsonResponse({"error": "Invalid role selected."}, status=400)

    if new_role == target.role:
        return JsonResponse({"error": "That's already their role."}, status=400)

    old_role = target.role
    target.role = new_role
    target.save(update_fields=["role"])

    logger.info(
        "Role changed for user '%s' (tenant %s): %s -> %s, by '%s'",
        target.username, request.user.tenant_id, old_role, new_role, request.user.username,
    )

    return JsonResponse({
        "success": True,
        "message": f"{target.username}'s role changed to {target.get_role_display()}.",
        "role": new_role,
        "role_display": target.get_role_display(),
    })


@login_required
@role_required("owner", "manager")
@require_POST
def edit_staff_outlet(request, user_id):
    """
    Reassigns an existing staff member to a different outlet within the same
    tenant. Scoped by tenant only (not the acting manager's own outlet),
    matching reset_staff_password/toggle_staff_active/edit_staff_role above —
    an owner managing several outlets moves people between them from any of
    their outlets' staff pages, not just the destination one.
    """
    target = get_object_or_404(
        User, id=user_id, tenant=request.user.tenant
    )
    if target.role == "owner":
        return JsonResponse({"error": "Owner's outlet can't be changed here."}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request."}, status=400)

    outlet_id = data.get("outlet_id")
    # Never trust the posted outlet_id without checking tenant ownership —
    # otherwise a crafted request could reassign staff onto another tenant's
    # outlet (FK integrity is satisfied, tenant isolation is not).
    try:
        new_outlet = Outlet.objects.get(id=outlet_id, tenant=request.user.tenant)
    except (Outlet.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"error": "Invalid outlet selected."}, status=400)

    if target.outlet_id == new_outlet.id:
        return JsonResponse({"error": "Staff member is already at that outlet."}, status=400)

    target.outlet = new_outlet
    target.save(update_fields=["outlet"])

    logger.info(
        "Outlet changed for user '%s' (tenant %s): -> '%s', by '%s'",
        target.username, request.user.tenant_id, new_outlet.name, request.user.username,
    )

    return JsonResponse({
        "success": True,
        "message": f"{target.username} moved to {new_outlet.name}.",
        "outlet_id": new_outlet.id,
        "outlet_name": new_outlet.name,
    })


@login_required
@role_required("owner", "manager")
@require_POST
def edit_pay_rate(request, user_id):
    """
    Sets or updates how a staff member is paid -- monthly salary or hourly
    rate -- feeding reports/services/labor_reports.py. Same tenant-scoping
    and owner-account boundary as edit_staff_role/edit_staff_outlet above.
    """
    from shifts.models import StaffPayRate

    target = get_object_or_404(
        User, id=user_id, tenant=request.user.tenant
    )
    if target.role == "owner":
        return JsonResponse({"error": "Owner's pay rate can't be set here."}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request."}, status=400)

    pay_type = data.get("pay_type")
    if pay_type not in ("monthly", "hourly"):
        return JsonResponse({"error": "Invalid pay type."}, status=400)

    from decimal import Decimal, InvalidOperation
    try:
        amount = Decimal(str(data.get("amount", "")))
    except InvalidOperation:
        return JsonResponse({"error": "Invalid amount."}, status=400)
    if amount <= 0:
        return JsonResponse({"error": "Amount must be positive."}, status=400)

    defaults = {
        "pay_type": pay_type,
        "monthly_salary": amount if pay_type == "monthly" else None,
        "hourly_rate": amount if pay_type == "hourly" else None,
        "updated_by": request.user,
    }
    rate, created = StaffPayRate.objects.update_or_create(
        tenant=request.user.tenant, staff=target, defaults=defaults,
    )

    logger.info(
        "Pay rate %s for user '%s' (tenant %s): %s -> %s, by '%s'",
        "set" if created else "updated",
        target.username, request.user.tenant_id, pay_type, amount, request.user.username,
    )

    return JsonResponse({
        "success": True,
        "message": f"{target.username}'s pay rate set to ₹{amount} ({rate.get_pay_type_display()}).",
    })


@login_required
@role_required("owner", "manager")
@require_POST
def set_default_station(request, station_id):

    station = KitchenStation.objects.get(
        id=station_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    # remove old default
    KitchenStation.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_default=True
    ).update(is_default=False)

    # set new default
    station.is_default = True
    station.save(update_fields=["is_default"])

    return redirect("/setup/kitchen-stations/")


@login_required
@role_required("owner", "manager")
@require_POST
def set_print_mode(request, mode):
    """
    'cashier' mode: clear printer IP from all non-default stations.
                    The default station keeps its IP (that's the cashier printer).
                    Result: print_bill_task detects no station printers → cashier strip.
    """
    if mode == "cashier":
        KitchenStation.objects.filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            is_default=False,
        ).update(printer_ip=None)
        return JsonResponse({"success": True, "message": "One Printer mode activated."})
    return JsonResponse({"error": "Unknown mode."}, status=400)


@login_required
@role_required("owner", "manager")
@require_POST
def delete_station(request, station_id):
    """
    Delete a kitchen station.
    Edge cases handled:
    - Cannot delete if it is the only station (KOT routing would have nowhere to go)
    - If deleting the default station, promote another station to default automatically
    - Menu items assigned to this station lose their station (fall to new default)
    """
    station = get_object_or_404(
        KitchenStation,
        id=station_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet,
    )

    # Edge case 1 — only station, refuse deletion
    total = KitchenStation.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True,
    ).count()
    if total <= 1:
        return JsonResponse(
            {"error": "Cannot delete the only kitchen station. Add another station first."},
            status=400,
        )

    was_default = station.is_default

    # Edge case 2 — deleting default station: reassign menu items + promote another
    if was_default:
        next_station = (
            KitchenStation.objects
            .filter(tenant=request.user.tenant, outlet=request.user.outlet, is_active=True)
            .exclude(id=station_id)
            .first()
        )
        if next_station:
            # Reassign any menu items pointing to this station
            from menu.models import MenuItem
            MenuItem.objects.filter(
                tenant=request.user.tenant,
                outlet=request.user.outlet,
                station=station,
            ).update(station=next_station)
            next_station.is_default = True
            next_station.save(update_fields=["is_default"])
    else:
        # Edge case 3 — non-default station with menu items assigned
        # Reassign those items to the default station
        from menu.models import MenuItem
        default = KitchenStation.objects.filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            is_default=True,
        ).first()
        if default:
            MenuItem.objects.filter(
                tenant=request.user.tenant,
                outlet=request.user.outlet,
                station=station,
            ).update(station=default)

    station_name = station.name
    station.delete()

    return JsonResponse({
        "success": True,
        "message": f"Station '{station_name}' deleted.",
        "was_default": was_default,
    })


@login_required
@role_required("owner", "manager")
@require_POST
def rename_table(request, table_id):
    try:
        data = json.loads(request.body)
        new_name = data.get("name", "").strip()
        if not new_name:
            return JsonResponse({"success": False, "error": "Name cannot be empty"})

        table = get_object_or_404(Table, id=table_id, tenant=request.user.tenant, outlet=request.user.outlet)
        table.name = new_name
        table.save(update_fields=["name"])
        return JsonResponse({"success": True})
    except Exception:
        logger.exception("Error renaming table")
        return JsonResponse({"success": False, "error": "Could not rename the table. Please try again."})

# ==================================
# OUTLET SETTINGS
# ==================================

@login_required
def printer_setup(request):
    """
    Printer setup for the phone-agent printing model.

    The Rasova app (or a PC poll-agent) polls /orders/agent/<key>/jobs/ and prints
    to the printer at the default Kitchen Station's ``printer_ip``. That IP is the
    ONLY field that actually drives printing — so this page reads/writes the default
    station, not the legacy ``outlet.agent_host``/``use_qz_tray`` WebSocket fields.

    ``outlet.printer_mac`` is still kept here: it's the printer's permanent ID, used
    for a router DHCP reservation so the IP never drifts.
    """
    if request.user.role not in ["owner", "manager"]:
        return redirect("/setup/")

    from setup.services.station_service import get_default_station

    outlet  = request.user.outlet
    station = get_default_station(request.user)
    saved   = False

    if request.method == "POST":
        # Printer IP — blank allowed (lets them clear it). Validate loosely; the
        # GenericIPAddressField rejects garbage on save.
        ip = (request.POST.get("printer_ip") or "").strip() or None
        station.printer_ip = ip

        port = str(request.POST.get("printer_port") or "9100").strip()
        station.printer_port = int(port) if port.isdigit() else 9100

        paper = str(request.POST.get("paper_width_mm") or "80").strip()
        station.paper_width_mm = int(paper) if paper in ("58", "80") else 80

        try:
            station.save(update_fields=["printer_ip", "printer_port", "paper_width_mm"])
        except Exception:
            messages.error(request, "That printer IP doesn't look valid — use a form like 192.168.1.100.")
            return render(request, "setup/printer_setup.html",
                          {"outlet": outlet, "station": station, "saved": False})

        # Mirror paper width + MAC onto the outlet for any code still reading them.
        outlet.printer_mac    = request.POST.get("printer_mac", "").strip().upper()
        outlet.paper_width_mm = station.paper_width_mm
        outlet.save(update_fields=["printer_mac", "paper_width_mm"])
        saved = True

    return render(request, "setup/printer_setup.html",
                  {"outlet": outlet, "station": station, "saved": saved})


@login_required
@require_POST
def printer_test_print(request):
    """
    Queue a test receipt the phone agent will print.

    Unlike ``test_print_station`` (which opens a socket FROM the server — impossible
    when the printer is on the cafe LAN), this enqueues a PrintJob exactly like a
    real receipt. The phone app / PC poll-agent picks it up within ~2s and prints it.
    """
    if request.user.role not in ["owner", "manager"]:
        return JsonResponse({"error": "Not allowed"}, status=403)

    from setup.services.station_service import get_default_station
    from printing.models import PrintJob

    station = get_default_station(request.user)
    if not station.printer_ip:
        return JsonResponse({"error": "Set a printer IP first, then test."}, status=422)

    import base64
    data_b64 = base64.b64encode(_build_test_receipt(station)).decode("ascii")

    job = PrintJob.objects.create(
        tenant  = request.user.tenant,
        outlet  = request.user.outlet,
        payload = {
            "data_b64":     data_b64,
            "network_host": station.printer_ip,
            "network_port": station.printer_port,
            "encoding":     station.printer_encoding,
        },
    )
    return JsonResponse({
        "success": True,
        "job_id": job.pk,
        "message": "Test queued — a receipt prints in a few seconds if your phone app is open and logged in.",
    })


def _build_test_receipt(station) -> bytes:
    """Raw ESC/POS bytes for a 'RASOVA TEST PRINT' page, sized to the station."""
    ESC, GS = b"\x1b", b"\x1d"
    enc   = station.printer_encoding or "cp437"
    chars = station.chars_per_line
    line  = ("-" * chars + "\n").encode(enc, errors="replace")

    buf = b""
    buf += ESC + b"@"                       # init
    buf += ESC + b"a" + b"\x01"             # center
    buf += GS  + b"!" + b"\x11"             # double width+height
    buf += "RASOVA\n".encode(enc, errors="replace")
    buf += GS  + b"!" + b"\x00"             # normal size
    buf += "TEST PRINT\n".encode(enc, errors="replace")
    buf += line
    buf += ESC + b"a" + b"\x00"             # left
    buf += f"Printer IP : {station.printer_ip}\n".encode(enc, errors="replace")
    buf += f"Port       : {station.printer_port}\n".encode(enc, errors="replace")
    buf += f"Paper      : {station.paper_width_mm}mm ({chars} chars)\n".encode(enc, errors="replace")
    buf += line
    buf += ESC + b"a" + b"\x01"             # center
    buf += "If you can read this,\nprinting works!\n".encode(enc, errors="replace")
    buf += b"\n\n\n\n\n\n"                  # feed past the cutter gap
    buf += GS + b"V" + b"\x00"              # full cut
    return buf


@login_required
def outlet_settings(request):
    """
    Outlet Settings page — owners can update all restaurant details
    (name, address, GSTIN, FSSAI, phone, WhatsApp, email) without
    touching Django admin.
    """
    if request.user.role not in ["owner", "manager"]:
        return redirect("/setup/")

    outlet = request.user.outlet
    tenant = request.user.tenant

    if request.method == "POST":
        # ── Outlet fields ──
        name = request.POST.get("outlet_name", "").strip()
        address = request.POST.get("address", "").strip()
        phone = request.POST.get("phone", "").strip()
        whatsapp_no = request.POST.get("whatsapp_no", "").strip()
        email = request.POST.get("email", "").strip()
        gst_no        = request.POST.get("gst_no",   "").strip().upper()
        fssai_no      = request.POST.get("fssai_no", "").strip()
        sac_code      = request.POST.get("sac_code", "996331").strip() or "996331"
        gst_inclusive = request.POST.get("gst_inclusive") == "true"

        if name:
            outlet.name = name
        outlet.address      = address
        outlet.phone        = phone
        outlet.email        = email
        outlet.gst_no       = gst_no
        outlet.fssai_no     = fssai_no
        outlet.sac_code     = sac_code
        outlet.gst_inclusive = gst_inclusive
        outlet.use_qz_tray            = "use_qz_tray"            in request.POST
        outlet.is_composition_scheme  = "is_composition_scheme"  in request.POST
        outlet.is_union_territory     = "is_union_territory"     in request.POST
        outlet.split_bill_by_category = "split_bill_by_category" in request.POST
        try:
            from decimal import Decimal
            outlet.parcel_charge_amount = Decimal(request.POST.get("parcel_charge_amount", "0") or "0")
        except Exception:
            logger.warning(
                "Could not parse parcel_charge_amount=%r for outlet %s — left unchanged",
                request.POST.get("parcel_charge_amount"), outlet.id,
            )

        # Store WhatsApp on the outlet (add field check)
        if hasattr(outlet, "whatsapp_no"):
            outlet.whatsapp_no = whatsapp_no

        # Operating hours — blank string → None so the field stays nullable
        opening_time = request.POST.get("opening_time", "").strip() or None
        closing_time = request.POST.get("closing_time", "").strip() or None
        outlet.opening_time = opening_time
        outlet.closing_time = closing_time

        # ── Printer settings ──
        outlet.printer_mac = request.POST.get("printer_mac", "").strip().upper()
        outlet.agent_host  = request.POST.get("agent_host", "localhost").strip() or "localhost"
        try:
            outlet.paper_width_mm = int(request.POST.get("paper_width_mm", 80))
        except (ValueError, TypeError):
            outlet.paper_width_mm = 80

        outlet.save()

        # ── Logo on Tenant ──
        if "logo" in request.FILES:
            tenant.logo = request.FILES["logo"]
            tenant.save(update_fields=["logo"])

        messages.success(request, "Outlet details saved successfully.")
        return redirect("outlet_settings")

    return render(request, "setup/outlet_settings.html", {
        "outlet": outlet,
        "tenant": tenant,
    })


@login_required
@tenant_required
def setup_qr_codes(request):
    """Print QR codes for all tables — one QR per table linking to the digital menu."""
    from django.conf import settings

    outlet = request.user.outlet
    tenant = request.user.tenant

    tables = Table.objects.filter(
        tenant=tenant, outlet=outlet
    ).order_by("name")

    base_url = getattr(settings, "BASE_URL", request.build_absolute_uri("/"))
    if not base_url.endswith("/"):
        base_url += "/"
    menu_base_url = f"{base_url}menu/"

    # QSR-only: link to the public "Now Serving" display board (see
    # tokens/views.py::display_board) -- fine-dining tenants don't use
    # tokens, so there's nothing to display. Same tenant types also get
    # an outlet-wide "Counter / Walk-in" QR (Outlet.qr_token) for outlets
    # with no seating at all, so there's still something to print even
    # when `tables` above is empty.
    display_board_url = None
    counter_qr_url = None
    if tenant.tenant_type in ["franchise", "cafe"]:
        from django.urls import reverse
        display_board_url = base_url.rstrip("/") + reverse(
            "display-board", args=[outlet.display_token]
        )
        counter_qr_url = f"{menu_base_url}{outlet.qr_token}/"

    return render(request, "setup/setup_qr_codes.html", {
        "tables":       tables,
        "menu_base_url": menu_base_url,
        "outlet":       outlet,
        "display_board_url": display_board_url,
        "counter_qr_url": counter_qr_url,
    })
