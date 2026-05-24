# setup/views/core_views.py
import json

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from orders.models import Table
from menu.models import MenuCategory, MenuItem
from tenants.models import Outlet
from setup.models import KitchenStation, PaymentConfig
from core.decorators import tenant_required
from accounts.models import User


# -------------------------------------------------
# MAIN SETUP DASHBOARD
# -------------------------------------------------

@login_required
def setup_wizard(request):

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
    """
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
        config.save()
        messages.success(request, "Payment methods saved.")
        return redirect("/tables/")

    return render(request, "setup/setup_payment_methods.html", {"config": config})


@login_required
def setup_staff(request):

    if request.user.role not in ["owner", "manager"]:
        return redirect("/dashboard/")

    tenant = request.user.tenant
    outlet = request.user.outlet

    staff = User.objects.filter(
        tenant=tenant
    ).exclude(role="owner")

    outlets = Outlet.objects.filter(tenant=tenant)

    if request.method == "POST":

        username = request.POST.get("username")
        password = request.POST.get("password")
        role = request.POST.get("role")

        if not username or not password:

            messages.error(request, "Username and password required")

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

    return render(
        request,
        "setup/setup_staff.html",
        {"staff": staff, "outlets": outlets}
    )


@login_required
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
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})

# ==================================
# OUTLET SETTINGS
# ==================================

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
        outlet.split_bill_by_category = "split_bill_by_category" in request.POST
        try:
            from decimal import Decimal
            outlet.parcel_charge_amount = Decimal(request.POST.get("parcel_charge_amount", "0") or "0")
        except Exception:
            pass

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
