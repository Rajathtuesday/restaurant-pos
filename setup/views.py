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

        config.save()
        messages.success(request, "Payment methods saved.")
        return redirect("/tables/")

    return render(request, "setup/setup_payment_methods.html", {"config": config})


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

    webhook_url = f"https://your-domain.com/orders/api/aggregator/webhook/"
    
    return render(request, "setup/aggregator_config.html", {
        "config": config,
        "webhook_url": webhook_url,
        "tenant_id": request.user.tenant.id,
        "outlet_id": request.user.outlet.id
    })