"""
Promo hardening and the discount rules (3 Oct 2026).

The promo review found, by running the code:
  F1  a promo's uses were counted per tap and never given back;
  F2  a bill didn't remember which promo it used, and promos were hard-deleted;
  F3  a cashier could give 100% off with no limit and no reason;
  F4  a second, older set of promo endpoints ignored the minimum, the cap
      and the scope;
  F5  the promo form saved an end date before the start, a cap of 0 and a
      negative minimum, and turned a bad date into a 500;
  F6  a promo's dates were judged by the calendar, so one valid until
      Saturday was dead at 1 am on Saturday night.
And two discount paths no screen uses, but anyone with a staff login could:
a bill discount and a dish discount sent with the order itself, with no
limit, no reason and no audit event.

Every test here fails on the code before this change.

Run: python manage.py test orders.tests.test_discount_hardening
"""
import json
from datetime import date, datetime, timezone as dt_tz
from decimal import Decimal as D
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.urls import NoReverseMatch, reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderEvent, OrderItem
from orders.services.void_service import cancel_whole_order
from promos.models import Promo
from reports.services.audit_reports import discount_void_audit
from tenants.models import Outlet, Tenant

IST = ZoneInfo("Asia/Kolkata")


class Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Hardening Pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.users = {
            role: User.objects.create_user(username=f"hd_{role}", password="pw", role=role,
                                           tenant=self.tenant, outlet=self.outlet)
            for role in ("owner", "manager", "cashier", "captain")
        }
        bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        self.pitcher = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=bar,
                                               name="Pitcher", price=D("1000"), gst_percentage=D("0"))
        self.order = self.new_order()

    def new_order(self):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.users["owner"],
                                     status="open", source="takeaway")
        OrderItem.objects.create(order=order, menu_item=self.pitcher, quantity=1, price=D("1000"),
                                 gst_percentage=D("0"), total_price=D("1000"), status="pending")
        order.recalculate_totals()
        return order

    def promo(self, name="Happy Hour", value="10", **fields):
        return Promo.objects.create(tenant=self.tenant, name=name, discount_type="percentage",
                                    discount_value=D(value), **fields)

    def post(self, role, url, body):
        client = Client()
        client.force_login(self.users[role])
        return client.post(url, data=json.dumps(body), content_type="application/json")

    def discount(self, role="cashier", order=None, **body):
        return self.post(role, reverse("apply-discount", args=[(order or self.order).id]), body)

    def comp(self, role="captain", **body):
        return self.post(role, reverse("make-complimentary", args=[self.order.items.get().id]), body)

    def item_discount(self, role="cashier", **body):
        return self.post(role, reverse("item-discount", args=[self.order.items.get().id]), body)

    def limit(self, pct):
        self.outlet.staff_discount_limit_pct = D(pct)
        self.outlet.save(update_fields=["staff_discount_limit_pct"])

    def uses(self, promo):
        promo.refresh_from_db()
        return promo.usage_count


class PromoUsesTest(Base):
    """F1: a use is taken once per bill, and given back whenever the promo
    stops applying before the bill is paid."""

    def test_tapping_the_same_promo_again_takes_one_use(self):
        promo = self.promo(max_uses=3)
        for _ in range(3):
            self.assertEqual(self.discount(promo_id=promo.id).status_code, 200)
        self.assertEqual(self.uses(promo), 1)

    def test_switching_promo_gives_the_first_one_back(self):
        first, second = self.promo("First"), self.promo("Second", "15")
        self.discount(promo_id=first.id)
        self.discount(promo_id=second.id)
        self.assertEqual((self.uses(first), self.uses(second)), (0, 1))
        self.order.refresh_from_db()
        self.assertEqual((self.order.promo, self.order.promo_name), (second, "Second"))

    def test_a_promo_that_fails_leaves_the_old_one_in_place(self):
        first = self.promo("First")
        too_big = self.promo("Big spenders", "20", min_order_value=D("5000"))
        self.discount(promo_id=first.id)
        resp = self.discount(promo_id=too_big.id)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual((self.uses(first), self.uses(too_big)), (1, 0))
        self.order.refresh_from_db()
        self.assertEqual(self.order.promo, first)

    def test_a_typed_discount_replaces_the_promo_and_gives_it_back(self):
        promo = self.promo()
        self.discount(promo_id=promo.id)
        self.discount(type="percentage", value=5, reason="regular guest")
        self.assertEqual(self.uses(promo), 0)
        self.order.refresh_from_db()
        self.assertEqual((self.order.promo, self.order.promo_name, self.order.discount_value), (None, "", D("5")))

    def test_removing_the_discount_gives_the_use_back(self):
        promo = self.promo()
        self.discount(promo_id=promo.id)
        self.discount(type="percentage", value=0)
        self.assertEqual(self.uses(promo), 0)

    def test_cancelling_the_bill_gives_the_use_back(self):
        promo = self.promo()
        self.discount(promo_id=promo.id)
        cancel_whole_order(self.users["manager"], self.order.id, reason="Guest left")
        self.assertEqual(self.uses(promo), 0)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.promo), ("cancelled", None))

    def test_a_paid_bill_keeps_its_use(self):
        promo = self.promo()
        self.discount(promo_id=promo.id)
        Order.objects.filter(pk=self.order.pk).update(status="paid")
        self.assertEqual(self.discount(type="percentage", value=0).status_code, 400)
        self.assertEqual(self.uses(promo), 1)

    def test_the_cap_counts_bills(self):
        promo = self.promo(max_uses=2)
        orders = [self.order, self.new_order(), self.new_order()]
        codes = [self.discount(order=o, promo_id=promo.id).status_code for o in orders]
        self.assertEqual(codes, [200, 200, 400])


class PromoRecordTest(Base):
    """F2: the bill remembers its promo, and archiving keeps the link."""

    def test_the_bill_and_the_audit_event_name_the_promo(self):
        promo = self.promo("Happy Hour", code="HH10")
        self.discount(promo_id=promo.id)
        self.order.refresh_from_db()
        self.assertEqual((self.order.promo, self.order.promo_name), (promo, "Happy Hour"))
        event = OrderEvent.objects.filter(order=self.order, event_type="discount_applied").get()
        self.assertEqual(
            {k: event.metadata[k] for k in ("via", "promo_id", "promo_name", "promo_code")},
            {"via": "promo", "promo_id": promo.id, "promo_name": "Happy Hour", "promo_code": "HH10"},
        )

    def test_renaming_the_promo_later_never_changes_the_bill(self):
        promo = self.promo("Happy Hour")
        self.discount(promo_id=promo.id)
        Promo.objects.filter(pk=promo.pk).update(name="Renamed")
        self.order.refresh_from_db()
        self.assertEqual(self.order.promo_name, "Happy Hour")

    def test_archiving_keeps_the_link_hides_it_and_frees_the_code(self):
        promo = self.promo("Happy Hour", code="HH")
        self.discount(promo_id=promo.id)
        resp = self.post("owner", reverse("promo_delete", args=[promo.id]), {})
        self.assertEqual(resp.status_code, 200)
        promo.refresh_from_db()
        self.assertIsNotNone(promo.archived_at)
        self.order.refresh_from_db()
        self.assertEqual(self.order.promo, promo)                      # still linked
        client = Client()
        client.force_login(self.users["cashier"])
        self.assertEqual(client.get(reverse("list-promos")).json()["promos"], [])
        self.assertEqual(self.discount(order=self.new_order(), promo_id=promo.id).status_code, 400)
        resp = self.post("owner", reverse("promo_create"), {
            "name": "Happy Hour 2", "code": "HH", "discount_type": "percentage", "discount_value": 15})
        self.assertEqual(resp.status_code, 200, resp.content)          # the code is free again


class PromoBusinessDayTest(Base):
    """F6: dates are the outlet's business day (until 6 am)."""

    def at(self, year, month, day, hour):
        moment = datetime(year, month, day, hour, 0, tzinfo=IST).astimezone(dt_tz.utc)
        return mock.patch("django.utils.timezone.now", return_value=moment)

    def test_a_promo_valid_until_saturday_still_works_at_1am(self):
        promo = self.promo(valid_until=date(2026, 10, 3))          # Saturday
        with self.at(2026, 10, 4, 1):                               # 1 am, still Saturday's night
            self.assertEqual(self.discount(promo_id=promo.id).status_code, 200)
            self.assertTrue(promo.is_live_for(self.outlet))
            client = Client()
            client.force_login(self.users["cashier"])
            self.assertEqual([p["id"] for p in client.get(reverse("list-promos")).json()["promos"]], [promo.id])

    def test_it_ends_when_the_business_day_ends(self):
        promo = self.promo(valid_until=date(2026, 10, 3))
        with self.at(2026, 10, 4, 7):
            resp = self.discount(promo_id=promo.id)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("expired", resp.json()["error"])

    def test_a_promo_starting_sunday_has_not_started_at_1am_sunday(self):
        promo = self.promo(valid_from=date(2026, 10, 4))
        with self.at(2026, 10, 4, 1):
            resp = self.discount(promo_id=promo.id)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("starts on", resp.json()["error"])


class PromoFormTest(Base):
    """F5 and F4: the Setup screen's form refuses what a typo gets wrong,
    and is the only way to create a promo."""

    def create(self, **fields):
        body = {"name": "Happy Hour", "discount_type": "percentage", "discount_value": 10, **fields}
        return self.post("owner", reverse("promo_create"), body)

    def test_mistakes_are_refused_with_a_message(self):
        cases = {
            "The start date must be a date like 2026-10-31.": {"valid_from": "garbage"},
            "The end date is before the start date.": {"valid_from": "2026-12-31", "valid_until": "2026-01-01"},
            "The usage cap must be at least 1, or blank for no cap.": {"max_uses": "0"},
            "The usage cap must be a whole number, or blank for no cap.": {"max_uses": "fifty"},
            "The minimum order can't be negative.": {"min_order_value": "-500"},
            "The discount can have at most 2 decimal places.": {"discount_value": "10.555"},
            "A percentage can't be more than 100.": {"discount_value": "150"},
        }
        for message, fields in cases.items():
            with self.subTest(fields=fields):
                resp = self.create(**fields)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["error"], message)
        self.assertFalse(Promo.objects.exists())

    def test_a_good_promo_keeps_its_minimum_cap_and_scope(self):
        resp = self.create(min_order_value="500", max_uses="10", all_outlets=True, code="hh")
        self.assertEqual(resp.status_code, 200, resp.content)
        promo = Promo.objects.get()
        self.assertEqual((promo.min_order_value, promo.max_uses, promo.outlet, promo.code),
                         (D("500"), 10, None, "HH"))

    def test_a_used_code_gets_a_clear_message(self):
        self.create(code="HH")
        resp = self.create(code="HH")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "The code HH is already used by another promo.")

    def test_the_old_duplicate_endpoints_are_gone(self):
        for name in ("create-promo", "toggle-promo", "delete-promo"):
            with self.assertRaises(NoReverseMatch):
                reverse(name, args=[1]) if name != "create-promo" else reverse(name)
        resp = self.post("owner", "/promos/create/", {"name": "x", "discount_type": "amount", "discount_value": 5})
        self.assertEqual(resp.status_code, 404)


class StaffLimitTest(Base):
    """F3: a cashier or captain may give up to the outlet's limit; above it
    a manager must. No limit set = the agreed uncapped authority."""

    def test_with_no_limit_cashier_and_captain_keep_their_authority(self):
        self.assertEqual(self.discount(type="percentage", value=50, reason="birthday").status_code, 200)
        self.assertEqual(self.comp(reason="food complaint").status_code, 200)

    def test_a_cashier_can_give_up_to_the_limit(self):
        self.limit("10")
        self.assertEqual(self.discount(type="percentage", value=10, reason="regular").status_code, 200)

    def test_above_the_limit_a_cashier_is_told_to_ask_a_manager(self):
        self.limit("10")
        resp = self.discount(type="percentage", value=15, reason="regular")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "A discount above 10% needs a manager. Ask a manager to apply it.")
        self.order.refresh_from_db()
        self.assertEqual(self.order.discount_value, D("0"))

    def test_a_flat_amount_is_judged_as_a_percent_of_the_bill(self):
        self.limit("10")
        self.assertEqual(self.discount(type="amount", value=150, reason="regular").status_code, 403)   # 15%
        self.assertEqual(self.discount(type="amount", value=100, reason="regular").status_code, 200)   # 10%

    def test_a_manager_and_the_owner_have_no_limit(self):
        self.limit("10")
        self.assertEqual(self.discount("manager", type="percentage", value=60, reason="owner's guest").status_code, 200)
        self.assertEqual(self.comp("owner", reason="owner's guest").status_code, 200)

    def test_with_a_limit_a_free_dish_needs_a_manager(self):
        self.limit("10")
        resp = self.comp(reason="birthday")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "A free dish above 10% needs a manager. Ask a manager to apply it.")
        self.assertFalse(self.order.items.get().is_complimentary)

    def test_a_dish_discount_follows_the_same_limit(self):
        self.limit("10")
        self.assertEqual(self.item_discount(percent=20, reason="regular").status_code, 403)
        self.assertEqual(self.item_discount(percent=10, reason="regular").status_code, 200)

    def test_a_promo_is_not_limited(self):
        self.limit("10")
        promo = self.promo("Ladies night", "50")
        self.assertEqual(self.discount(promo_id=promo.id).status_code, 200)

    def test_the_promo_values_are_used_not_what_the_screen_sends(self):
        promo = self.promo("Small", "5")
        self.discount(promo_id=promo.id, type="percentage", value=100)
        self.order.refresh_from_db()
        self.assertEqual(self.order.discount_value, D("5"))


class ReasonTest(Base):
    """F3: a discount given by hand says why; a promo doesn't need to."""

    def test_a_typed_discount_needs_a_reason(self):
        for reason in (None, "", "  ", "ok"):
            with self.subTest(reason=reason):
                resp = self.discount(type="percentage", value=10, reason=reason)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["error"],
                                 "Give a reason for the discount (for example: regular guest, food complaint).")

    def test_the_reason_goes_on_the_audit_event(self):
        self.discount(type="percentage", value=10, reason="  regular   guest ")
        event = OrderEvent.objects.get(order=self.order, event_type="discount_applied")
        self.assertEqual((event.metadata["via"], event.metadata["reason"]), ("manual", "regular guest"))

    def test_a_free_dish_and_a_dish_discount_need_a_reason(self):
        self.assertEqual(self.comp().status_code, 400)
        self.assertEqual(self.item_discount(percent=10).status_code, 400)
        self.assertEqual(self.item_discount(percent=0).status_code, 200)        # taking it off needs none
        self.assertEqual(self.comp(reason="birthday").status_code, 200)
        event = OrderEvent.objects.get(order=self.order, event_type="item_complimentary")
        self.assertEqual(event.metadata["reason"], "birthday")

    def test_removing_and_promos_need_no_reason(self):
        self.assertEqual(self.discount(promo_id=self.promo().id).status_code, 200)
        self.assertEqual(self.discount(type="percentage", value=0).status_code, 200)


class HiddenPathsTest(Base):
    """The two discount paths no screen uses: they now follow the same rules,
    and a refusal undoes the whole request."""

    def create(self, role="cashier", **extra):
        body = {"cart": [{"id": self.pitcher.id, "quantity": 1}], "source": "takeaway", **extra}
        return self.post(role, reverse("create-order"), body)

    def test_a_bill_discount_with_the_order_needs_a_reason(self):
        before = Order.objects.count()
        resp = self.create(discount_type="percentage", discount_value="10")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Order.objects.count(), before)                         # rolled back

    def test_with_a_reason_it_is_applied_and_audited(self):
        resp = self.create(discount_type="percentage", discount_value="10", discount_reason="regular guest")
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.discount_total, D("100.00"))
        event = OrderEvent.objects.get(order=order, event_type="discount_applied")
        self.assertEqual((event.metadata["via"], event.metadata["reason"]), ("order_api", "regular guest"))

    def test_above_the_limit_the_whole_order_is_refused(self):
        self.limit("10")
        before = Order.objects.count()
        resp = self.create(discount_type="percentage", discount_value="50", discount_reason="birthday")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("needs a manager", resp.json()["error"])
        self.assertEqual(Order.objects.count(), before)

    def test_a_dish_discount_in_the_cart_follows_the_rules(self):
        cart = [{"id": self.pitcher.id, "quantity": 1, "discount_pct": 20}]
        self.assertEqual(self.create(cart=cart).status_code, 400)                 # no reason
        cart[0]["discount_reason"] = "regular guest"
        resp = self.create(cart=cart)
        self.assertEqual(resp.status_code, 200, resp.content)
        line = OrderItem.objects.get(order_id=resp.json()["order_id"])
        self.assertEqual(line.item_discount_pct, D("20"))
        event = OrderEvent.objects.get(order_id=resp.json()["order_id"], event_type="item_discount_applied")
        self.assertEqual((event.metadata["via"], event.metadata["reason"]), ("cart", "regular guest"))
        self.limit("10")
        self.assertEqual(self.create(cart=cart).status_code, 400)


class AuditReportTest(Base):
    """The audit report shows the reasons and the promos, and doesn't count
    taking a discount off as a discount."""

    def test_reasons_promos_and_no_removals(self):
        self.discount(type="percentage", value=10, reason="regular guest")
        self.discount(type="percentage", value=0)                                   # removal
        self.discount(promo_id=self.promo("Happy Hour").id)
        self.comp(reason="birthday")
        # An event written before reasons existed still counts as a discount.
        OrderEvent.objects.create(tenant=self.tenant, outlet=self.outlet, order=self.order,
                                  event_type="discount_applied", created_by=self.users["manager"],
                                  metadata={"action": "discount_applied", "type": "percentage", "value": "5"})
        from core.utils import get_business_date
        today = get_business_date(None, self.outlet)
        report = discount_void_audit(self.tenant, self.outlet, today, today)
        self.assertEqual(sum(r["count"] for r in report["discounts"]), 3)          # manual + promo + old
        self.assertEqual([(r["metadata__reason"], r["count"]) for r in report["discount_reasons"]],
                         [("regular guest", 1)])
        self.assertEqual([(r["metadata__reason"], r["count"]) for r in report["comp_reasons"]], [("birthday", 1)])
        self.assertEqual([(r["metadata__promo_name"], r["count"]) for r in report["promos_used"]],
                         [("Happy Hour", 1)])
