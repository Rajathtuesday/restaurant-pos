# setup/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse

from orders.models import Table
from menu.models import MenuCategory, MenuItem
from tenants.models import Outlet
from django.views.decorators.http import require_POST
from setup.models import KitchenStation, PaymentConfig
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
        
        KitchenStation.objects.create(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            name=name.strip()
        )

        messages.success(request, "Kitchen station created")

        return redirect("/setup/kitchen-stations/")  # ✅ FIX

    return render(
        request,
        "setup/setup_kitchen_stations.html",
        {
            "stations": stations
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
    ip = request.POST.get("printer_ip", "").strip() or None
    port = request.POST.get("printer_port", "9100").strip()
    paper = request.POST.get("paper_width_mm", "80").strip()
    cut = request.POST.get("cut_type", "full").strip()
    enc = request.POST.get("printer_encoding", "cp437").strip()

    station.printer_ip = ip
    station.printer_port = int(port) if port.isdigit() else 9100
    station.paper_width_mm = int(paper) if paper in ("58", "80") else 80
    station.cut_type = cut if cut in ("full", "partial", "none") else "full"
    station.printer_encoding = enc if enc in ("cp437", "cp850", "utf-8") else "cp437"
    station.save(update_fields=["printer_ip", "printer_port", "paper_width_mm", "cut_type", "printer_encoding"])

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
    config, _ = PaymentConfig.objects.get_or_create(
        tenant=tenant,
        outlet=outlet
    )

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


import json
from accounts.models import User


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
def rename_table(request, table_id):
    import json
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
        gst_no = request.POST.get("gst_no", "").strip().upper()
        fssai_no = request.POST.get("fssai_no", "").strip()

        if name:
            outlet.name = name
        outlet.address = address
        outlet.phone = phone
        outlet.email = email
        outlet.gst_no = gst_no
        outlet.fssai_no = fssai_no

        # Store WhatsApp on the outlet (add field check)
        if hasattr(outlet, "whatsapp_no"):
            outlet.whatsapp_no = whatsapp_no

        # Operating hours — blank string → None so the field stays nullable
        opening_time = request.POST.get("opening_time", "").strip() or None
        closing_time = request.POST.get("closing_time", "").strip() or None
        outlet.opening_time = opening_time
        outlet.closing_time = closing_time

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


# ==================================
# PROMO / DISCOUNT MANAGEMENT
# ==================================

from core.decorators import tenant_required

@login_required
@tenant_required
def setup_promos(request):
    """
    Full-page promo management UI inside the Setup area.
    Accessible by owner and manager.
    """
    if request.user.role not in ["owner", "manager"]:
        return redirect("/setup/")

    from orders.models import Promo
    from tenants.models import Outlet

    tenant = request.user.tenant
    outlet = request.user.outlet

    # All outlets for this tenant (used by the "All Outlets" toggle)
    all_outlets = Outlet.objects.filter(tenant=tenant).order_by("name")

    # Promos scoped to this tenant (includes all-outlet ones + this outlet's)
    promos = Promo.objects.filter(tenant=tenant).select_related("outlet").order_by("-created_at")

    return render(request, "setup/setup_promos.html", {
        "promos": promos,
        "outlets": all_outlets,
        "current_outlet": outlet,
    })


@login_required
@tenant_required
@require_POST
def promo_create(request):
    """JSON endpoint — create a new promo."""
    if request.user.role not in ["owner", "manager"]:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from orders.models import Promo
    from tenants.models import Outlet
    from decimal import Decimal, InvalidOperation
    from django.db import IntegrityError

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name           = data.get("name", "").strip()
    code           = data.get("code", "").strip().upper()
    description    = data.get("description", "").strip()
    discount_type  = data.get("discount_type", "percentage")
    all_outlets_flag = data.get("all_outlets", False)

    if not name:
        return JsonResponse({"error": "Name is required"}, status=400)
    if discount_type not in ("percentage", "amount"):
        return JsonResponse({"error": "Invalid discount type"}, status=400)

    try:
        discount_value  = Decimal(str(data.get("discount_value", "0")))
        min_order_value = Decimal(str(data.get("min_order_value", "0")))
    except InvalidOperation:
        return JsonResponse({"error": "Invalid numeric value"}, status=400)

    if discount_value <= 0:
        return JsonResponse({"error": "Discount value must be positive"}, status=400)
    if discount_type == "percentage" and discount_value > 100:
        return JsonResponse({"error": "Percentage cannot exceed 100"}, status=400)

    max_uses    = data.get("max_uses") or None
    valid_from  = data.get("valid_from") or None
    valid_until = data.get("valid_until") or None

    # Resolve outlet — None = all outlets
    outlet = None if all_outlets_flag else request.user.outlet

    # Validate outlet_id override (owner-only: pick a specific outlet)
    outlet_id = data.get("outlet_id")
    if outlet_id and not all_outlets_flag:
        try:
            outlet = Outlet.objects.get(id=outlet_id, tenant=request.user.tenant)
        except Outlet.DoesNotExist:
            return JsonResponse({"error": "Outlet not found"}, status=404)

    try:
        promo = Promo.objects.create(
            tenant          = request.user.tenant,
            outlet          = outlet,
            name            = name,
            code            = code,
            description     = description,
            discount_type   = discount_type,
            discount_value  = discount_value,
            min_order_value = min_order_value,
            max_uses        = int(max_uses) if max_uses else None,
            valid_from      = valid_from or None,
            valid_until     = valid_until or None,
        )
    except IntegrityError as e:
        return JsonResponse({"error": f"Database error: {str(e)}"}, status=409)
    except Exception as e:
        return JsonResponse({"error": f"Unexpected error: {str(e)}"}, status=500)

    return JsonResponse({
        "success": True,
        "id":      promo.id,
        "name":    promo.name,
        "scope":   promo.outlet.name if promo.outlet_id else "All Outlets",
    })


@login_required
@require_POST
def promo_toggle(request, promo_id):
    """Toggle is_active on a promo."""
    if request.user.role not in ["owner", "manager"]:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from orders.models import Promo
    try:
        promo = Promo.objects.get(id=promo_id, tenant=request.user.tenant)
    except Promo.DoesNotExist:
        return JsonResponse({"error": "Promo not found"}, status=404)

    promo.is_active = not promo.is_active
    promo.save(update_fields=["is_active"])
    return JsonResponse({"success": True, "is_active": promo.is_active})


@login_required
@require_POST
def promo_delete(request, promo_id):
    """Hard-delete a promo."""
    if request.user.role not in ["owner", "manager"]:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from orders.models import Promo
    try:
        promo = Promo.objects.get(id=promo_id, tenant=request.user.tenant)
    except Promo.DoesNotExist:
        return JsonResponse({"error": "Promo not found"}, status=404)

    promo.delete()
    return JsonResponse({"success": True})


# ==================================
# AGGREGATOR CONFIG
# ==================================
from core.decorators import tenant_required

@login_required
@tenant_required
def aggregator_setup(request):
    if request.user.role != "owner":
        return redirect("setup_wizard")

    from setup.models import AggregatorConfig
    config, created = AggregatorConfig.objects.get_or_create(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    if request.method == "POST":
        config.zomato_enabled = request.POST.get("zomato_enabled") == "on"
        config.swiggy_enabled = request.POST.get("swiggy_enabled") == "on"
        config.uber_eats_enabled = request.POST.get("uber_eats_enabled") == "on"
        config.auto_accept_orders = request.POST.get("auto_accept_orders") == "on"
        
        config.zomato_webhook_secret = request.POST.get("zomato_webhook_secret")
        config.swiggy_webhook_secret = request.POST.get("swiggy_webhook_secret")
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

    config, _ = AggregatorConfig.objects.get_or_create(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
    )

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

    import logging
    logging.getLogger("pos.orders").info(
        "Aggregator toggle | outlet=%s | platform=%s | enabled=%s | user=%s",
        request.user.outlet.id, platform, enabled, request.user.username
    )

    return JsonResponse({
        "success":        True,
        "zomato_enabled": config.zomato_enabled,
        "swiggy_enabled": config.swiggy_enabled,
    })

# -------------------------------------------------
# ONBOARDING WIZARD  — /setup/onboard/?step=N
# 5 steps: Restaurant Info → Menu → Staff → Tables → Payment
# -------------------------------------------------

@login_required
def onboarding_wizard(request):
    tenant = request.user.tenant
    outlet = request.user.outlet
    step   = int(request.GET.get("step", 1))

    # ── STEP 1: Restaurant info ──────────────────
    if step == 1:
        if request.method == "POST":
            tenant.name        = request.POST.get("name", tenant.name).strip() or tenant.name
            tenant.tenant_type = request.POST.get("tenant_type", tenant.tenant_type)
            if "logo" in request.FILES:
                tenant.logo = request.FILES["logo"]
            tenant.save(update_fields=["name", "tenant_type", "logo"])

            outlet.address  = request.POST.get("address", "").strip()
            outlet.phone    = request.POST.get("phone", "").strip()
            outlet.gst_no   = request.POST.get("gst_no", "").strip().upper()
            outlet.fssai_no = request.POST.get("fssai_no", "").strip()
            outlet.save(update_fields=["address", "phone", "gst_no", "fssai_no"])

            return redirect(f"/setup/onboard/?step=2")

    # ── STEP 2: First menu items ─────────────────
    elif step == 2:
        if request.method == "POST":
            cat_name = request.POST.get("category", "").strip()
            if cat_name:
                cat, _ = MenuCategory.objects.get_or_create(
                    tenant=tenant, outlet=outlet, name=cat_name,
                    defaults={"is_active": True}
                )
                for i in range(1, 4):
                    iname  = request.POST.get(f"item_{i}_name", "").strip()
                    iprice = request.POST.get(f"item_{i}_price", "").strip()
                    if iname and iprice:
                        try:
                            from decimal import Decimal
                            MenuItem.objects.get_or_create(
                                tenant=tenant, outlet=outlet,
                                name=iname, category=cat,
                                defaults={"price": Decimal(iprice), "is_available": True}
                            )
                        except Exception:
                            pass
            return redirect(f"/setup/onboard/?step=3")

    # ── STEP 3: First staff member ───────────────
    elif step == 3:
        if request.method == "POST":
            from accounts.models import User
            uname = request.POST.get("username", "").strip()
            fname = request.POST.get("first_name", "").strip()
            lname = request.POST.get("last_name", "").strip()
            role  = request.POST.get("role", "cashier")
            pwd   = request.POST.get("password", "").strip()
            if uname and pwd:
                try:
                    if not User.objects.filter(username=uname, tenant=tenant).exists():
                        User.objects.create_user(
                            username=uname, password=pwd,
                            first_name=fname, last_name=lname,
                            tenant=tenant, outlet=outlet, role=role
                        )
                except Exception:
                    pass
            return redirect(f"/setup/onboard/?step=4")

    # ── STEP 4: Tables (skip for QSR/Café) ──────
    elif step == 4:
        if request.method == "POST":
            if tenant.tenant_type == "fine_dining":
                table_names = [
                    request.POST.get(f"table_{i}", "").strip()
                    for i in range(1, 6)
                ]
                for tname in table_names:
                    if tname:
                        Table.objects.get_or_create(
                            tenant=tenant, outlet=outlet, name=tname,
                            defaults={"is_active": True}
                        )
            return redirect(f"/setup/onboard/?step=5")

    # ── STEP 5: Payment + done ───────────────────
    elif step == 5:
        if request.method == "POST":
            config, _ = PaymentConfig.objects.get_or_create(
                tenant=tenant, outlet=outlet
            )
            config.upi_enabled  = "upi"  in request.POST.getlist("methods")
            config.cash_enabled = "cash" in request.POST.getlist("methods")
            config.card_enabled = "card" in request.POST.getlist("methods")
            config.upi_id = request.POST.get("upi_id", "").strip().lower()
            config.save()
            # Mark onboarding complete in session
            request.session["onboarding_done"] = True
            return redirect("/dashboard/")

    # Progress calculation for the progress bar
    progress = {
        1: MenuCategory.objects.filter(tenant=tenant, outlet=outlet).exists(),
        2: MenuItem.objects.filter(tenant=tenant, outlet=outlet).exists(),
        3: tenant.users.filter(outlet=outlet).exclude(role="owner").exists(),
        4: Table.objects.filter(tenant=tenant, outlet=outlet).exists()
               if tenant.tenant_type == "fine_dining" else True,
    }
    done_count = sum(1 for v in progress.values() if v)

    config, _ = PaymentConfig.objects.get_or_create(tenant=tenant, outlet=outlet)

    return render(request, "setup/onboard.html", {
        "step": step,
        "total_steps": 5,
        "done_count": done_count,
        "tenant": tenant,
        "outlet": outlet,
        "config": config,
        "is_qsr": tenant.tenant_type in ("franchise", "cafe"),
    })
