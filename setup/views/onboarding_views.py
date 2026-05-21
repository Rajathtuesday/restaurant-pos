# setup/views/onboarding_views.py
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from orders.models import Table
from menu.models import MenuCategory, MenuItem
from setup.models import PaymentConfig
from core.decorators import tenant_required
from accounts.models import User
from tenants.models import Tenant, RESERVED_SLUGS


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

            # Custom subdomain — validate and apply if provided
            custom_slug = slugify(request.POST.get("custom_slug", "").strip())
            if custom_slug and custom_slug != tenant.slug:
                slug_ok = (
                    custom_slug not in RESERVED_SLUGS
                    and not Tenant.objects.filter(slug=custom_slug).exclude(id=tenant.id).exists()
                    and len(custom_slug) >= 3
                )
                if slug_ok:
                    tenant.slug = custom_slug

            tenant.save(update_fields=["name", "tenant_type", "logo", "slug"])

            outlet.address      = request.POST.get("address", "").strip()
            outlet.phone        = request.POST.get("phone", "").strip()
            outlet.gst_no       = request.POST.get("gst_no", "").strip().upper()
            outlet.fssai_no     = request.POST.get("fssai_no", "").strip()
            outlet.gst_inclusive = request.POST.get("gst_inclusive") == "true"
            outlet.save(update_fields=["address", "phone", "gst_no", "fssai_no", "gst_inclusive"])

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
                table_count = request.POST.get("table_count", "").strip()
                table_prefix = request.POST.get("table_prefix", "T").strip() or "T"
                if table_count:
                    try:
                        count = int(table_count)
                        if 1 <= count <= 200:
                            for i in range(1, count + 1):
                                tname = f"{table_prefix}{i}"
                                Table.objects.get_or_create(
                                    tenant=tenant, outlet=outlet, name=tname,
                                    defaults={"is_active": True}
                                )
                    except (ValueError, TypeError):
                        pass
                custom_name = request.POST.get("custom_table_name", "").strip()
                if custom_name:
                    Table.objects.get_or_create(
                        tenant=tenant, outlet=outlet, name=custom_name,
                        defaults={"is_active": True}
                    )
            return redirect(f"/setup/onboard/?step=5")

    # ── STEP 5: Payment + done ───────────────────
    elif step == 5:
        if request.method == "POST":
            config, _ = PaymentConfig.for_outlet(outlet, tenant)
            config.upi_enabled  = "upi"  in request.POST.getlist("methods")
            config.cash_enabled = "cash" in request.POST.getlist("methods")
            config.card_enabled = "card" in request.POST.getlist("methods")
            config.upi_id = request.POST.get("upi_id", "").strip().lower()
            config.save()
            # Mark onboarding complete in session
            request.session["onboarding_done"] = True
            return redirect("/dashboard/")

    # Progress calculation for the progress bar
    from accounts.models import User
    progress = {
        1: MenuCategory.objects.filter(tenant=tenant, outlet=outlet).exists(),
        2: MenuItem.objects.filter(tenant=tenant, outlet=outlet).exists(),
        3: User.objects.filter(tenant=tenant, outlet=outlet).exclude(role="owner").exists(),
        4: Table.objects.filter(tenant=tenant, outlet=outlet).exists()
               if tenant.tenant_type == "fine_dining" else True,
    }
    done_count = sum(1 for v in progress.values() if v)

    config, _ = PaymentConfig.for_outlet(outlet, tenant)

    return render(request, "setup/onboard.html", {
        "step": step,
        "total_steps": 5,
        "done_count": done_count,
        "tenant": tenant,
        "outlet": outlet,
        "config": config,
        "is_qsr": tenant.tenant_type in ("franchise", "cafe"),
    })


@login_required
@tenant_required
@require_POST
def sample_menu(request):
    from decimal import Decimal
    tenant = request.user.tenant
    outlet = request.user.outlet

    sample_data = [
        ("Starters", [
            ("Veg Spring Roll", Decimal("120")),
            ("Paneer Tikka", Decimal("180")),
        ]),
        ("Main Course", [
            ("Dal Makhani", Decimal("180")),
            ("Butter Chicken", Decimal("280")),
            ("Veg Pulao", Decimal("160")),
        ]),
        ("Beverages", [
            ("Lassi", Decimal("80")),
            ("Cold Coffee", Decimal("120")),
            ("Fresh Lime Soda", Decimal("70")),
        ]),
    ]

    for cat_name, items in sample_data:
        cat, _ = MenuCategory.objects.get_or_create(
            tenant=tenant, outlet=outlet, name=cat_name,
            defaults={"is_active": True}
        )
        for item_name, price in items:
            MenuItem.objects.get_or_create(
                tenant=tenant, outlet=outlet, name=item_name, category=cat,
                defaults={"price": price, "is_available": True}
            )

    return redirect("/setup/onboard/?step=3")


@login_required
@tenant_required
def checklist_status(request):
    tenant = request.user.tenant
    outlet = request.user.outlet

    info_done = bool(outlet.phone or outlet.gst_no)
    menu_done = MenuItem.objects.filter(tenant=tenant, outlet=outlet).exists()
    if tenant.tenant_type == "fine_dining":
        tables_done = Table.objects.filter(tenant=tenant, outlet=outlet).exists()
    else:
        tables_done = True
    staff_done = User.objects.filter(tenant=tenant, outlet=outlet).exclude(role="owner").exists()
    payment_done = PaymentConfig.objects.filter(tenant=tenant, outlet=outlet).exists()

    steps = [
        {"key": "info",    "label": "Restaurant info",     "done": info_done,    "url": "/setup/onboard/?step=1"},
        {"key": "menu",    "label": "Menu items",          "done": menu_done,    "url": "/menu/"},
        {"key": "tables",  "label": "Tables / counters",   "done": tables_done,  "url": "/setup/onboard/?step=4"},
        {"key": "staff",   "label": "Staff accounts",      "done": staff_done,   "url": "/setup/staff/"},
        {"key": "payment", "label": "Payment methods",     "done": payment_done, "url": "/setup/onboard/?step=5"},
    ]
    done_count = sum(1 for s in steps if s["done"])
    all_done = done_count == len(steps)

    return JsonResponse({"steps": steps, "all_done": all_done, "done_count": done_count})


def check_slug_available(request):
    """AJAX — returns whether a slug is available for use as a subdomain."""
    raw  = request.GET.get("slug", "").strip()
    slug = slugify(raw)
    tenant = getattr(request.user, "tenant", None) if request.user.is_authenticated else None

    if not slug:
        return JsonResponse({"available": False, "reason": "Enter a subdomain"})
    if len(slug) < 3:
        return JsonResponse({"available": False, "reason": "Too short — minimum 3 characters"})
    if slug in RESERVED_SLUGS:
        return JsonResponse({"available": False, "reason": f"'{slug}' is reserved"})

    qs = Tenant.objects.filter(slug=slug)
    if tenant:
        qs = qs.exclude(id=tenant.id)
    if qs.exists():
        return JsonResponse({"available": False, "reason": "Already taken — try another"})

    return JsonResponse({"available": True, "slug": slug})
