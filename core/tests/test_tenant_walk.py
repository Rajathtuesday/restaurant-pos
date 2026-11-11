"""
Every URL in Rasova, opened by restaurant A's owner and pointed at
restaurant B's rows. Not one byte of B may come back.

Why this exists (11 Nov 2026): TenantScopedModel auto-filters by the
request's tenant, so most pages are safe by construction. But 26 models
are plain models.Model (OrderItem, Payment, Modifier, Recipe, OfferWindow
and friends) and are only safe when every view reaches them through a
scoped parent. One Modifier.objects.get(id=...) in a view is a
cross-restaurant leak, and a POS that leaks one restaurant's bills to
another is finished. Until now that was checked by discipline and a few
hand-written tests. This walks the whole URL map instead, so a new page
with a forgotten filter fails here, in CI, before it reaches anyone.

How it works
  * Two restaurants are built with the same shape. Every text field of B
    carries MARKER, a string that appears nowhere else.
  * Every URL pattern is visited as A's owner (A has every feature turned
    on, so no feature gate hides a page). A pattern that takes an id gets
    B's id for that kind of row (the REGISTRY below says which).
  * A page is public by design (login, the guest menu, the token display
    board, the webhooks) or superuser-only (admin) and is skipped; the
    skip list is explicit and a stale entry fails the test.
  * The leak check: MARKER in the body or the redirect, a 500, or a 2xx
    or 4xx other than 403/404/405 on a B id. A 200 on B's id fails even
    with no marker in it, because a JSON of B's totals has no names in it.

The last test proves the detector itself: a deliberately leaky view is
wired in and the walk must catch it.

Run: python manage.py test core.tests.test_tenant_walk
"""
import re
from datetime import date, time, timedelta
from decimal import Decimal

from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, path, reverse
from django.utils import timezone

from accounts.models import User
from core.features import get_all_known_features
from core.testing import freeze_ratelimit_clock
from core.utils import get_business_date
from crm.models import Guest, Reservation
from finance.models import Expense
from inventory.models import (
    BarCount, BatchTransfer, InventoryItem, ProductionBatch, PurchaseOrder,
    RecipeImportJob, RecipeImportLine, StockRequisition, Supplier,
)
from kitchen.models import KitchenMessage, KOTBatch
from menu.models import MenuCategory, MenuItem, Modifier, ModifierGroup
from offers.models import Offer
from orders.models import Order, OrderItem, Payment, Table
from payments.models import RazorpayQRCode, Refund
from printing.models import PrintJob
from promos.models import Promo
from setup.models import KitchenStation, PaymentConfig, ScheduledReportSubscription
from shifts.models import Shift, ShiftTemplate, StaffSchedule
from tenants.models import Outlet, Tenant, TenantFeatureOverride
from tokens.models import TokenOrder
from waiter.models import WaiterCall

# Appears in every text field of restaurant B and nowhere else. Letters
# only, so it survives HTML escaping, JSON encoding and slugs.
MARKER = "ZZBSEVENCTHREEENINE"

# Pages that show no restaurant's private data, or show B's data to B's
# own guests by design (a guest with B's QR token must see B's menu). Each
# entry names a URL pattern and says why it is public. A name that no
# longer exists fails the test, so the list cannot rot.
PUBLIC_BY_DESIGN = {
    "landing": "marketing site",
    "health_check": "load balancer probe",
    "demo_switch": "demo mode toggle, no tenant data",
    "service_worker": "static JS",
    "agent_download": "print agent installer download",
    "login": "login page",
    "logout": "logs out",
    "live-demo": "logs in to the demo restaurant",
    "public-bill": "guest's own bill via a signed token",
    "public-feedback": "guest feedback via a signed token",
    "order_status": "guest's own order via a signed token",
    "display-board": "token display screen keyed by the outlet's display token",
    "display-data": "token display screen data",
    "display-stream": "token display screen stream",
    "digital_menu": "guest menu on the tenant's own subdomain",
    "menu_qr": "guest menu on the tenant's own subdomain",
    "menu_view": "guest menu keyed by the table's QR token",
    "call_waiter": "guest waiter call keyed by the table's QR token",
    "api-ingest-order": "aggregator webhook, key authenticated",
    "razorpay-webhook": "Razorpay webhook, signature authenticated",
    "billing-webhook": "Razorpay subscription webhook, signature authenticated",
    "print-queue-add": "print agent, agent key authenticated",
    "print-queue-poll": "print agent, agent key authenticated",
    "print-queue-done": "print agent, agent key authenticated",
    "print-queue-failed": "print agent, agent key authenticated",
    "box_setup": "offline box first run, not served in the cloud",
    "box_health": "offline box probe",
}

# Unnamed patterns, by route, that are public: static files and redirects.
PUBLIC_ROUTES = {"robots.txt", "sitemap.xml", "favicon.ico", "manifest.json", "compare/petpooja/"}


def _text(value):
    """A unique B-marked string for a name field; the suffix keeps unique
    columns (slug, username, batch number) apart."""
    return f"{MARKER}{value}"


class Restaurant:
    """One complete restaurant: every kind of row a URL can point at.

    `tag` is woven into every text field, so a response that contains B's
    tag has leaked. The same builder makes A (so A's owner can log in and
    reach every page) and B (the victim)."""

    def __init__(self, tag, slug):
        self.tag = tag
        self.tenant = Tenant.objects.create(name=f"{tag} Restaurant", slug=slug, tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name=f"{tag} Outlet")
        PaymentConfig.objects.create(tenant=self.tenant, outlet=self.outlet, cash_enabled=True)
        for feature in get_all_known_features():
            TenantFeatureOverride.objects.create(tenant=self.tenant, feature=feature, enabled=True)

        self.owner = User.objects.create_user(
            username=f"{slug}-owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet, first_name=f"{tag} Owner",
        )
        self.staff = User.objects.create_user(
            username=f"{slug}-waiter", password="pw", role="waiter",
            tenant=self.tenant, outlet=self.outlet, first_name=f"{tag} Waiter",
        )

        self.station = KitchenStation.objects.create(
            tenant=self.tenant, outlet=self.outlet, name=f"{tag} Station", is_default=True, is_active=True,
        )
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name=f"{tag} Starters")
        self.menu_item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name=f"{tag} Paneer Tikka", price=Decimal("250.00"), gst_percentage=Decimal("5"),
            station=self.station,
        )
        self.modifier_group = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name=f"{tag} Spice")
        self.modifier = Modifier.objects.create(group=self.modifier_group, name=f"{tag} Extra Hot", price=Decimal("10"))

        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name=f"{tag} Table 1")
        self.table2 = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name=f"{tag} Table 2")

        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table,
            created_by=self.owner, status="open", source="dine_in", customer_name=f"{tag} Customer",
        )
        self.order_item = OrderItem.objects.create(
            order=self.order, menu_item=self.menu_item, quantity=2,
            price=self.menu_item.price, gst_percentage=self.menu_item.gst_percentage,
            total_price=self.menu_item.price * 2, notes=f"{tag} no onions",
        )
        self.order.recalculate_totals()
        self.payment = Payment.objects.create(
            order=self.order, method="cash", amount=Decimal("100.00"), created_by=self.owner,
            reference=f"{tag}-ref",
        )
        self.refund = Refund.objects.create(
            payment=self.payment, order=self.order, amount=Decimal("50.00"),
            reason=f"{tag} refund reason", refunded_by=self.owner,
        )
        self.qr = RazorpayQRCode.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=self.order,
            qr_code_id=f"qr_{slug}", image_url="https://rzp.io/x.png",
            quoted_amount=self.order.grand_total, status="active",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.kot = KOTBatch.objects.create(tenant=self.tenant, outlet=self.outlet, order=self.order,
                                           station=self.station, kot_number=1)
        self.kitchen_message = KitchenMessage.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=self.order, message=f"{tag} hurry please",
        )
        self.token_order = TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=self.order, token_number=7,
            date=get_business_date(timezone.now(), self.outlet),
        )
        self.print_job = PrintJob.objects.create(
            tenant=self.tenant, outlet=self.outlet, payload={"text": f"{tag} receipt"},
        )
        self.report_subscription = ScheduledReportSubscription.objects.create(
            tenant=self.tenant, outlet=self.outlet, recipient_emails=f"{slug}@example.com", created_by=self.owner,
        )
        self.promo = Promo.objects.create(
            tenant=self.tenant, outlet=self.outlet, name=f"{tag} Promo", discount_value=Decimal("10"),
        )
        # A valid offer, as Offer.clean() would insist: a percent-off offer
        # with no percent is a row the setup page cannot describe.
        self.offer = Offer.objects.create(
            tenant=self.tenant, outlet=self.outlet, name=f"{tag} Offer", kind="percent_off",
            percent=Decimal("10"), created_by=self.owner,
        )

        self.supplier = Supplier.objects.create(tenant=self.tenant, outlet=self.outlet, name=f"{tag} Supplier")
        self.inventory_item = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name=f"{tag} Paneer", unit="kg",
            preferred_supplier=self.supplier,
        )
        self.purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, supplier=self.supplier, notes=f"{tag} PO notes",
        )
        self.requisition = StockRequisition.objects.create(
            tenant=self.tenant, requesting_outlet=self.outlet, created_by=self.owner, notes=f"{tag} req notes",
        )
        self.batch = ProductionBatch.objects.create(
            tenant=self.tenant, batch_number=f"{tag}-BATCH-1", source_outlet=self.outlet, created_by=self.owner,
        )
        self.transfer = BatchTransfer.objects.create(
            tenant=self.tenant, batch=self.batch, destination_outlet=self.outlet,
        )
        self.recipe_job = RecipeImportJob.objects.create(
            tenant=self.tenant, outlet=self.outlet, menu_item=self.menu_item, created_by=self.owner,
        )
        self.recipe_line = RecipeImportLine.objects.create(
            job=self.recipe_job, match_method="manual", raw_ingredient_name=f"{tag} ingredient",
        )
        self.bar_count = BarCount.objects.create(
            tenant=self.tenant, outlet=self.outlet, business_date=date.today(), counted_by=self.owner,
        )

        self.shift = Shift.objects.create(tenant=self.tenant, outlet=self.outlet, staff=self.staff,
                                          notes=f"{tag} shift notes")
        self.shift_template = ShiftTemplate.objects.create(
            tenant=self.tenant, outlet=self.outlet, name=f"{tag} Morning",
            start_time=time(9, 0), end_time=time(17, 0),
        )
        self.schedule = StaffSchedule.objects.create(
            tenant=self.tenant, outlet=self.outlet, staff=self.staff, date=date.today(),
            template=self.shift_template,
        )
        self.guest = Guest.objects.create(tenant=self.tenant, phone=f"9{abs(hash(slug)) % 10**9:09d}",
                                          name=f"{tag} Guest")
        self.reservation = Reservation.objects.create(
            tenant=self.tenant, outlet=self.outlet, guest=self.guest, table=self.table,
            reservation_time=timezone.now() + timedelta(days=1), created_by=self.owner,
            notes=f"{tag} reservation notes",
        )
        self.expense = Expense.objects.create(
            tenant=self.tenant, outlet=self.outlet, category="supplies", amount=Decimal("500"),
            expense_date=date.today(), created_by=self.owner, notes=f"{tag} expense",
        )
        self.waiter_call = WaiterCall.objects.create(tenant=self.tenant, outlet=self.outlet, table=self.table)


# URL kwarg name -> B's value. A kwarg name that means different things in
# different apps (item_id is a menu item, an inventory item or an order
# item) is picked by the route's first segment. A kwarg missing here fails
# the walk, so a new URL must say what it points at.
REGISTRY = {
    "order_id": lambda b, route: b.order.id,
    "item_id": lambda b, route: (
        b.menu_item.id if route.startswith("menu/")
        else b.inventory_item.id if route.startswith("inventory/")
        else b.order_item.id
    ),
    "job_id": lambda b, route: b.recipe_job.id if route.startswith("inventory/") else b.print_job.id,
    "user_id": lambda b, route: b.staff.id,
    "station_id": lambda b, route: b.station.id,
    "req_id": lambda b, route: b.requisition.id,
    "po_id": lambda b, route: b.purchase_order.id,
    "modifier_id": lambda b, route: b.modifier.id,
    "batch_id": lambda b, route: b.batch.id,
    "promo_id": lambda b, route: b.promo.id,
    "offer_id": lambda b, route: b.offer.id,
    "token_id": lambda b, route: b.token_order.id,
    "tenant_id": lambda b, route: b.tenant.id,
    "table_id": lambda b, route: b.table.id,
    "subscription_id": lambda b, route: b.report_subscription.id,
    "refund_id": lambda b, route: b.refund.id,
    "kot_id": lambda b, route: b.kot.id,
    "category_id": lambda b, route: b.category.id,
    "transfer_id": lambda b, route: b.transfer.id,
    "template_id": lambda b, route: b.shift_template.id,
    "supplier_id": lambda b, route: b.supplier.id,
    "shift_id": lambda b, route: b.shift.id,
    "schedule_id": lambda b, route: b.schedule.id,
    "reservation_id": lambda b, route: b.reservation.id,
    "primary_id": lambda b, route: b.table.id,
    "payment_id": lambda b, route: b.payment.id,
    "message_id": lambda b, route: b.kitchen_message.id,
    "line_id": lambda b, route: b.recipe_line.id,
    "guest_id": lambda b, route: b.guest.id,
    "group_id": lambda b, route: b.modifier_group.id,
    "expense_id": lambda b, route: b.expense.id,
    "count_id": lambda b, route: b.bar_count.id,
    "call_id": lambda b, route: b.waiter_call.id,
    "qr_code_id": lambda b, route: b.qr.qr_code_id,
    "task_id": lambda b, route: "no-such-task",
    "mode": lambda b, route: "agent",
}

# Kwargs that are not a row id: a value, not something B owns. A URL whose
# only kwargs are these is walked for leaks and crashes, but a 400 on it is
# the view saying the value is wrong, which is right.
NOT_IDS = {"task_id", "mode"}

# Statuses that mean "refused" when the URL carried B's id. Anything else
# (200, 400, 500) means the view got further than it should have.
REFUSED = {302, 403, 404, 405}


def _routes(patterns, prefix="", namespace=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            ns = f"{namespace}:{p.namespace}" if namespace and p.namespace else (p.namespace or namespace)
            yield from _routes(p.url_patterns, prefix + str(p.pattern), ns)
        elif isinstance(p, URLPattern):
            yield namespace, p.name, prefix + str(p.pattern)


def walk(victim, as_user):
    """Visits every non-admin URL as `as_user`, pointing ids at `victim`.
    Returns (findings, visited) where findings is a list of one-line
    problems and visited the number of URLs checked."""
    findings = []
    visited = 0
    seen_public = set()
    for namespace, name, route in _routes(get_resolver().url_patterns):
        if namespace.startswith("admin") or route.startswith("admin/"):
            continue
        if route.startswith("^media/"):
            continue    # Django's own file serving, present only with DEBUG on
        if name in PUBLIC_BY_DESIGN:
            seen_public.add(name)
            continue
        if route in PUBLIC_ROUTES:
            seen_public.add(route)
            continue
        kwargs_spec = re.findall(r"<(?:\w+:)?(\w+)>", route)
        unmapped = [k for k in kwargs_spec if k not in REGISTRY]
        if unmapped:
            findings.append(f"UNMAPPED {route}: no B value for {unmapped}; add it to REGISTRY or PUBLIC_BY_DESIGN")
            continue
        if not name:
            findings.append(f"UNNAMED {route}: give it a name or list it in PUBLIC_ROUTES")
            continue
        kwargs = {k: REGISTRY[k](victim, route) for k in kwargs_spec}
        try:
            url = reverse(f"{namespace}:{name}" if namespace else name, kwargs=kwargs)
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            findings.append(f"REVERSE {route}: {e}")
            continue

        visited += 1
        # A crash is a finding, not the end of the walk.
        client = Client(raise_request_exception=False)
        client.force_login(as_user)
        response = client.get(url, HTTP_ACCEPT="text/html,application/json")
        if response.status_code == 405:
            # An empty JSON object: the JSON views parse it, the form views
            # see an empty POST. Either way the body gives nothing away.
            response = client.post(url, "{}", content_type="application/json",
                                   HTTP_ACCEPT="text/html,application/json")
        status = response.status_code
        body = response.content.decode("utf-8", "ignore") if not response.streaming else ""
        location = response.get("Location", "")

        if status >= 500:
            exc = getattr(response, "exc_info", None)
            where = ""
            if exc:
                tb = exc[2]
                while tb and tb.tb_next:
                    tb = tb.tb_next
                frame = tb.tb_frame.f_code if tb else None
                where = f": {exc[0].__name__}: {exc[1]}" + (
                    f" at {frame.co_filename.split('pos')[-1]}:{tb.tb_lineno}" if frame else "")
            findings.append(f"CRASH {status} {url} ({name}){where}")
        if MARKER in body or MARKER in location:
            findings.append(f"LEAK {url} ({name}): {status} and B's marker is in the response")
        if (set(kwargs) - NOT_IDS) and status not in REFUSED:
            findings.append(f"REACHED {url} ({name}): {status} on B's id, expected one of {sorted(REFUSED)}; "
                            f"body starts {body[:120]!r}")
        if status == 429:
            findings.append(f"RATELIMITED {url} ({name}): inconclusive")

    stale = (set(PUBLIC_BY_DESIGN) | PUBLIC_ROUTES) - seen_public
    if stale:
        findings.append(f"STALE public list entries, remove them: {sorted(stale)}")
    return findings, visited


class TenantWalkTest(TestCase):
    """The walk against the real URL map."""

    @classmethod
    def setUpTestData(cls):
        cls.a = Restaurant("Alpha", "walk-alpha")
        cls.b = Restaurant(MARKER, "walk-bravo")

    def setUp(self):
        freeze_ratelimit_clock(self)

    def test_the_other_restaurant_is_never_visible(self):
        findings, visited = walk(self.b, self.a.owner)
        self.assertEqual(
            findings, [],
            "\n\nTenant isolation walk found problems:\n  " + "\n  ".join(findings),
        )
        # 241 on 11 Nov 2026: the URL map less admin and the public pages.
        self.assertGreater(visited, 200, "the walk covered too few URLs; did the URL map move?")

    def test_b_is_marked_everywhere_the_walk_looks(self):
        """The detector depends on B's rows carrying the marker; a builder
        change that drops it would make the walk pass for the wrong reason."""
        for attr in ("tenant", "outlet", "category", "menu_item", "modifier", "table", "supplier",
                     "inventory_item", "promo", "offer", "guest", "shift_template", "station"):
            self.assertIn(MARKER, getattr(self.b, attr).name, attr)
        self.assertIn(MARKER, self.b.order_item.notes)
        self.assertIn(MARKER, self.b.kitchen_message.message)
        self.assertIn(MARKER, self.b.expense.notes)
        self.assertIn(MARKER, self.b.print_job.payload["text"])


def _leaky_view(request, order_id):
    """What a forgotten filter looks like: fetches by id alone."""
    order = Order.objects.unscoped(reason="deliberately leaky test view").get(id=order_id)
    return HttpResponse(f"Table: {order.table.name}")


def _quiet_view(request, order_id):
    """What a view with a filter but no error page looks like: answers 200
    with B's id and nothing of B's in the body."""
    Order.objects.filter(id=order_id, tenant=request.user.tenant).first()
    return HttpResponse('{"total": "250.00"}', content_type="application/json")


urlpatterns = [
    path("leak/<int:order_id>/", _leaky_view, name="walk-test-leak"),
    path("quiet/<int:order_id>/", _quiet_view, name="walk-test-quiet"),
]


@override_settings(ROOT_URLCONF=__name__)
class TenantWalkDetectsLeaksTest(TestCase):
    """Proves the walk catches both ways a view can fail: B's data in the
    body, and a 200 on B's id with nothing readable in it."""

    @classmethod
    def setUpTestData(cls):
        cls.a = Restaurant("Alpha", "walk-alpha")
        cls.b = Restaurant(MARKER, "walk-bravo")

    def test_a_leaky_view_and_a_quiet_200_are_both_caught(self):
        findings, visited = walk(self.b, self.a.owner)
        self.assertEqual(visited, 2)
        kinds = sorted(f.split(" ")[0] for f in findings if not f.startswith("STALE"))
        self.assertEqual(kinds, ["LEAK", "REACHED", "REACHED"], findings)
        self.assertTrue(any("walk-test-leak" in f and f.startswith("LEAK") for f in findings), findings)
        self.assertTrue(any("walk-test-quiet" in f for f in findings), findings)
