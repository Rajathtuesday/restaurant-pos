# tenants/services/tenant_config_service.py
"""
Single source of truth for tenant-configuration admin logic — shared by
portal/views.py and accounts/views/superuser_views.py.

Both apps used to carry their own hand-copied version of this: the same
five POST actions (update_outlet, update_printer, add_station,
update_payment, add_staff), the same feature-summary logic, and the same
preset library. They drifted apart over time without anyone noticing:
the superuser panel's update_outlet was missing parcel_charge_amount,
is_composition_scheme, and split_bill_by_category entirely, its feature
summary couldn't even display whether composition_scheme or parcel_charge
were on, and its PRESETS dict had a different set of presets from
portal's (missing "counter_billing", carrying a "qsr_kds" portal never
had) — so applying the "same" preset from the two panels didn't actually
produce the same result. Same root cause as the table-state and
cancel-item bugs from earlier this session: two implementations of one
feature, edited independently, silently disagreeing.

Everything here is now the one place that logic lives. Both views call
into these functions and only differ in which template they render.
"""
import logging
from decimal import Decimal

from accounts.models import User
from setup.models import KitchenStation, PaymentConfig
from tenants.models import TenantFeatureOverride, TenantFeatureAuditLog

logger = logging.getLogger("pos.tenants")


# ---------------------------------------------------------------------------
# PRESETS — merged from both previously-independent copies. Portal's set
# was the more complete one (icon/color, outlet-field overrides, and
# parcel_charge/composition_scheme in every enable list) — used as the
# template for every preset, including "qsr_kds", which only existed in
# the superuser copy and has been brought up to the same shape.
# ---------------------------------------------------------------------------
PRESETS = {
    "qsr_no_kds": {
        "label": "QSR — counter, no kitchen screen",
        "icon":  "bi-ticket-perforated",
        "color": "#10b981",
        "enable":  ["token_system", "kot_system", "inventory", "reports",
                    "ai_menu_import", "direct_billing_mode",
                    "parcel_charge", "composition_scheme"],
        "disable": ["kitchen_display", "floor_plan", "waiter_call",
                    "split_bill", "merge_tables", "crm", "reservations",
                    "counter_billing"],
        "outlet":  {"split_bill_by_category": False},
    },
    "qsr_kds": {
        "label": "QSR — with kitchen display",
        "icon":  "bi-display",
        "color": "#0ea5e9",
        "enable":  ["token_system", "kot_system", "kitchen_display",
                    "inventory", "reports", "ai_menu_import",
                    "direct_billing_mode", "parcel_charge", "composition_scheme"],
        "disable": ["floor_plan", "waiter_call", "split_bill",
                    "merge_tables", "crm", "reservations", "counter_billing"],
        "outlet":  {"split_bill_by_category": False},
    },
    "counter_billing": {
        "label": "Counter Billing — multi-section hotel / food court",
        "icon":  "bi-receipt-cutoff",
        "color": "#6366f1",
        "enable":  ["token_system", "kot_system", "inventory", "reports",
                    "ai_menu_import", "counter_billing",
                    "parcel_charge", "composition_scheme"],
        "disable": ["kitchen_display", "floor_plan", "waiter_call",
                    "split_bill", "merge_tables", "crm", "reservations"],
        "outlet":  {"split_bill_by_category": True},
    },
    "fine_dining": {
        "label": "Fine Dining — full table service",
        "icon":  "bi-table",
        "color": "#c5a059",
        "enable":  ["floor_plan", "waiter_call", "kitchen_display", "kot_system",
                    "merge_tables", "split_bill", "qr_menu", "running_order",
                    "inventory", "reports", "ai_menu_import", "crm",
                    "composition_scheme", "parcel_charge"],
        "disable": ["token_system", "simple_billing", "direct_billing_mode",
                    "barcode_transfer", "counter_billing"],
        "outlet":  {"split_bill_by_category": False},
    },
    "cafe": {
        "label": "Café — mixed counter + tables",
        "icon":  "bi-cup-hot",
        "color": "#f59e0b",
        "enable":  ["token_system", "floor_plan", "qr_menu", "waiter_call",
                    "kot_system", "kitchen_display", "inventory", "reports",
                    "ai_menu_import", "parcel_charge", "composition_scheme"],
        "disable": ["merge_tables", "split_bill", "crm", "reservations",
                    "barcode_transfer", "counter_billing"],
        "outlet":  {"split_bill_by_category": False},
    },
}

# The full feature set shown on a tenant's config screen. Superuser's copy
# was missing counter_billing, composition_scheme, and parcel_charge —
# meaning those three could be silently on or off with no way to see it
# from that panel.
KEY_FEATURES = [
    "token_system", "floor_plan", "kitchen_display", "kot_system",
    "inventory", "waiter_call", "qr_menu", "direct_billing_mode",
    "counter_billing", "composition_scheme", "parcel_charge",
]


def get_feature_summary(tenant):
    """[{key, on}] for every feature in KEY_FEATURES, override-aware."""
    from core.features import TENANT_FEATURES

    overrides = {o.feature: o.enabled for o in TenantFeatureOverride.objects.filter(tenant=tenant)}
    default_features = set(TENANT_FEATURES.get(tenant.tenant_type, []))

    def feat_on(key):
        return overrides[key] if key in overrides else key in default_features

    return [{"key": k, "on": feat_on(k)} for k in KEY_FEATURES]


def update_outlet_from_post(outlet, post):
    """Applies the "update_outlet" POST action. Saves the outlet."""
    outlet.phone       = post.get("phone", "").strip() or None
    outlet.gst_no      = post.get("gst_no", "").strip().upper() or None
    outlet.fssai_no    = post.get("fssai_no", "").strip() or None
    outlet.address     = post.get("address", "").strip()
    outlet.sac_code    = post.get("sac_code", "996331").strip() or "996331"
    outlet.gst_inclusive          = post.get("gst_inclusive") == "true"
    outlet.is_composition_scheme  = "is_composition_scheme" in post
    outlet.split_bill_by_category = "split_bill_by_category" in post
    try:
        outlet.parcel_charge_amount = Decimal(post.get("parcel_charge_amount", "0") or "0")
    except Exception:
        # Bad input (e.g. non-numeric) silently leaves the field at its
        # previous value instead of erroring — that's an intentional
        # best-effort default, but it was invisible when it happened.
        logger.warning(
            "Could not parse parcel_charge_amount=%r for outlet %s — left unchanged",
            post.get("parcel_charge_amount"), outlet.id,
        )
    outlet.save()


def update_printer_from_post(tenant, post):
    """Applies the "update_printer" POST action. Returns the saved station."""
    from django.shortcuts import get_object_or_404
    station = get_object_or_404(KitchenStation, id=post.get("station_id"), tenant=tenant)
    station.printer_ip     = post.get("printer_ip", "").strip() or None
    station.printer_port   = int(post.get("printer_port") or 9100)
    station.paper_width_mm = int(post.get("paper_width_mm") or 80)
    station.cut_type       = post.get("cut_type", "partial")
    station.save()
    return station


def add_station_from_post(tenant, outlet, post):
    """Applies the "add_station" POST action. Returns the new station, or None if no name given."""
    sname = post.get("station_name", "").strip()
    if not sname:
        return None
    return KitchenStation.objects.create(tenant=tenant, outlet=outlet, name=sname, is_default=False)


def update_payment_from_post(config, post):
    """Applies the "update_payment" POST action. Saves the config."""
    config.cash_enabled = "cash" in post.getlist("methods")
    config.upi_enabled  = "upi"  in post.getlist("methods")
    config.card_enabled = "card" in post.getlist("methods")
    config.upi_id       = post.get("upi_id", "").strip().lower()
    config.save()


def update_subscription_from_post(tenant, post):
    """Applies the "update_subscription" POST action. Saves the tenant.
    Manual override for a superuser -- sits alongside, not instead of, the
    automated billing.tasks (generate_monthly_invoices /
    enforce_overdue_subscriptions)."""
    from decimal import Decimal
    try:
        tenant.subscription_fee = Decimal(post.get("subscription_fee", "0") or "0")
    except Exception:
        logger.warning("Could not parse subscription_fee=%r for tenant %s — left unchanged",
                        post.get("subscription_fee"), tenant.id)
    new_status = post.get("subscription_status")
    if new_status in dict(tenant._meta.get_field("subscription_status").choices):
        tenant.subscription_status = new_status
    tenant.save()


def mark_invoice_paid_manually(invoice):
    """Applies the "mark_paid" POST action for a bank-transfer/cash payment
    made outside the automated Razorpay Payment Link flow. Extends the
    tenant's subscription the same way the real webhook does, so a manual
    payment has the exact same downstream effect as an automated one."""
    from django.utils import timezone
    invoice.status = "paid"
    invoice.paid_at = timezone.now()
    invoice.save(update_fields=["status", "paid_at"])

    tenant = invoice.tenant
    tenant.subscription_end_date = invoice.period_end
    if tenant.subscription_status != "active":
        tenant.subscription_status = "active"
    tenant.save(update_fields=["subscription_end_date", "subscription_status"])


def add_staff_from_post(tenant, outlet, post):
    """Applies the "add_staff" POST action. Returns the new user, or None if invalid/duplicate."""
    uname = post.get("username", "").strip()
    role  = post.get("role", "cashier")
    pwd   = post.get("password", "").strip()
    if not (uname and pwd) or User.objects.filter(username=uname).exists():
        return None
    return User.objects.create_user(username=uname, password=pwd, tenant=tenant, outlet=outlet, role=role)


def apply_preset_to_tenant(tenant, preset_key, changed_by):
    """
    Applies a named preset: feature overrides (with audit log entries,
    same as a single toggle_feature_flag call) and any outlet field
    overrides the preset specifies. Returns the preset dict, or None if
    preset_key isn't recognized. Caller is responsible for wrapping in
    transaction.atomic() if that guarantee is needed.
    """
    preset = PRESETS.get(preset_key)
    if not preset:
        return None

    for feature in preset.get("enable", []):
        TenantFeatureOverride.objects.update_or_create(
            tenant=tenant, feature=feature, defaults={"enabled": True},
        )
        TenantFeatureAuditLog.objects.create(
            tenant=tenant, feature=feature, enabled=True, source=f"preset:{preset_key}",
            changed_by=changed_by, notes=f"Applied by {changed_by.username} via preset '{preset_key}'",
        )
    for feature in preset.get("disable", []):
        TenantFeatureOverride.objects.update_or_create(
            tenant=tenant, feature=feature, defaults={"enabled": False},
        )
        TenantFeatureAuditLog.objects.create(
            tenant=tenant, feature=feature, enabled=False, source=f"preset:{preset_key}",
            changed_by=changed_by, notes=f"Applied by {changed_by.username} via preset '{preset_key}'",
        )

    outlet = tenant.outlets.first()
    if outlet and preset.get("outlet"):
        for field, val in preset["outlet"].items():
            setattr(outlet, field, val)
        outlet.save()

    return preset
