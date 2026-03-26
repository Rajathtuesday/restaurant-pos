# setup/views.py
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction

from orders.models import Table
from menu.models import MenuCategory, MenuItem
from tenants.models import Outlet
from django.views.decorators.http import require_POST
from setup.models import KitchenStation
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
            count = int(request.POST.get("table_count"))
        except:
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
def setup_kitchen_stations(request, station_id):

    """
    Kitchen stations will later connect to 
    """

    stations = KitchenStation.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    if request.method == "POST":

        name = request.POST.get("station_name")

        if not name:
            messages.error(request, "Station name required")
            return redirect("setup_kitchen_stations")

        KitchenStation.objects.create(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            name=name
        )

        messages.success(request, "Kitchen station created")

        return redirect("setup_kitchen_stations")

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
    For MVP we store payment methods in session
    Later this should be a PaymentConfig model
    """

    if request.method == "POST":

        methods = request.POST.getlist("methods")

        if not methods:
            messages.error(request, "Select at least one method")
            return redirect("setup_payment_methods")

        request.session["payment_methods"] = methods

        messages.success(request, "Payment methods saved")

        return redirect("/tables/")

    return render(request, "setup/setup_payment_methods.html")


from accounts.models import User
from django.contrib.auth.hashers import make_password


@login_required
def setup_staff(request):

    if request.user.role not in ["owner", "manager"]:
        return redirect("/dashboard/")

    tenant = request.user.tenant
    outlet = request.user.outlet

    staff = User.objects.filter(
        tenant=tenant,
        outlet=outlet
    ).exclude(role="owner")

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

        User.objects.create(

            username=username,

            password=make_password(password),

            role=role,

            tenant=tenant,

            outlet=outlet

        )

        messages.success(request, "Staff account created")

        return redirect("setup_staff")

    return render(
        request,
        "setup/setup_staff.html",
        {"staff": staff}
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