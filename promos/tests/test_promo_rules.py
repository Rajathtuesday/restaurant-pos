"""
Promo review, round 2 (18 Oct 2026).

Every finding here was proved on the code of 18 Oct by running it, and
every test fails on that code:

  P1  a promo's minimum order was checked only when it was applied: void
      dishes afterwards and the promo stayed ("Rs 200 off above Rs 1,000"
      on a bill voided down to Rs 200 made it Rs 0);
  P8  the same through a free dish;
  P9  at an outlet whose prices include GST, the minimum was compared with
      the value before tax, so a Rs 1,000 bill was "below Rs 1,000";
  P2  sending an order with a 0 discount left the promo on the bill, its
      use taken and nothing off;
  P3  the audit report counted promo taps, not bills;
  P7  no bill said which promo it got;
  P4  the Setup page called a promo that hasn't started, or is used up,
      "Expired";
  P5  an archived promo could be switched back on;
  P6  a manager could make promos for another outlet, or for all of them.

And what the owner decided (18 Oct): below the minimum a promo pauses
(stays on the bill, takes nothing off, comes back by itself); managers
manage only their own outlet's promos; a flat promo needs a minimum at
least as big as itself; promos can be edited.

The three root fixes these test: one module sets a bill's discount
(orders/services/bill_discount.py); a promo's minimum is part of the bill's
maths (tax_engine.compute, discount_min_order); a promo's uses are counted
from the bills that hold it (Promo.uses).

Run: python manage.py test promos
"""
import threading
from datetime import timedelta
from decimal import Decimal as D
from unittest import skipUnless

from django.db import IntegrityError, connection, transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse

from orders.models import Order, OrderItem
from orders.services.bill_discount import PromoRefused, set_promo
from orders.services.bill_layout import bill_layout
from orders.services.void_service import cancel_whole_order, void_order_item
from orders.tests.test_discount_hardening import Base
from promos.models import Promo
from reports.services.audit_reports import discount_void_audit
from tenants.models import SAMPLE_GSTIN, Outlet


class PromoBase(Base):
    def add(self, price, gst="0", order=None):
        order = order or self.order
        item = OrderItem.objects.create(order=order, menu_item=self.pitcher, quantity=1, price=D(price),
                                        gst_percentage=D(gst), total_price=D(price), status="pending")
        order.refresh_from_db()
        order.recalculate_totals()
        return item

    def flat(self, value="200", minimum="1000", **fields):
        return Promo.objects.create(tenant=self.tenant, name=fields.pop("name", "Big Spender"),
                                    discount_type="amount", discount_value=D(value),
                                    min_order_value=D(minimum), **fields)

    def fresh(self, order=None):
        order = order or self.order
        order.refresh_from_db()
        return order

    def today(self):
        from core.utils import get_business_date
        return get_business_date(None, self.outlet)


# ── Root B: the minimum follows the bill ─────────────────────────────

class MinimumOrderTest(PromoBase):

    def setUp(self):
        super().setUp()
        self.small = self.add("200")                       # bill: 1000 + 200
        self.promo_ = self.flat()
        self.assertEqual(self.discount(promo_id=self.promo_.id).status_code, 200)
        self.assertEqual(self.fresh().grand_total, D("1000.00"))

    def test_voiding_below_the_minimum_pauses_the_promo(self):
        """P1: the bill must not drop to 0."""
        void_order_item(self.users["manager"], self.order.items.get(price=D("1000")).id, "wrong dish")
        order = self.fresh()
        self.assertEqual((order.discount_total, order.grand_total), (D("0.00"), D("200.00")))
        self.assertEqual(order.promo, self.promo_)          # paused, not removed
        self.assertTrue(order.promo_paused)

    def test_adding_dishes_back_brings_the_promo_back(self):
        void_order_item(self.users["manager"], self.order.items.get(price=D("1000")).id, "wrong dish")
        self.add("900")                                     # 200 + 900 = 1100
        order = self.fresh()
        self.assertEqual((order.discount_total, order.grand_total), (D("200.00"), D("900.00")))
        self.assertFalse(order.promo_paused)

    def test_a_free_dish_below_the_minimum_pauses_the_promo(self):
        """P8."""
        big = self.order.items.get(price=D("1000"))
        resp = self.post("manager", reverse("make-complimentary", args=[big.id]), {"reason": "birthday"})
        self.assertEqual(resp.status_code, 200, resp.content)
        order = self.fresh()
        self.assertEqual((order.discount_total, order.grand_total), (D("0.00"), D("200.00")))

    def test_a_paused_promo_keeps_its_use(self):
        Promo.objects.filter(pk=self.promo_.pk).update(max_uses=1)
        void_order_item(self.users["manager"], self.order.items.get(price=D("1000")).id, "wrong dish")
        other = self.new_order()
        self.add("500", order=other)
        resp = self.discount(order=other, promo_id=self.promo_.id)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("limit", resp.json()["error"].lower())

    def test_a_promo_is_still_refused_below_the_minimum_when_applied(self):
        other = self.new_order()                            # 1000 only
        void_order_item(self.users["manager"], other.items.get().id, "wrong dish")
        self.add("300", order=other)
        resp = self.discount(order=other, promo_id=self.promo_.id)
        self.assertEqual(resp.status_code, 400)
        self.assertIsNone(self.fresh(other).promo)


class MinimumWithTaxInsideTest(PromoBase):
    """P9: the minimum is the dishes' menu value, what the guest reads on
    the menu, whether or not the price includes GST."""

    def test_a_menu_total_at_the_minimum_qualifies(self):
        self.outlet.gst_inclusive = True
        self.outlet.gst_no = SAMPLE_GSTIN                   # registered: it collects GST
        self.outlet.save(update_fields=["gst_inclusive", "gst_no"])
        order = self.new_order()
        OrderItem.objects.filter(order=order).update(gst_percentage=D("5"))
        order.recalculate_totals()
        self.assertLess(self.fresh(order).subtotal, D("1000"))     # 952.38: the value before tax
        resp = self.discount(order=order, promo_id=self.flat(minimum="1000").id)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.fresh(order).grand_total, D("800.00"))


class EngineMinimumTest(PromoBase):
    """The rule lives in the one place every total comes from."""

    def test_compute_gives_the_bill_discount_only_at_the_minimum(self):
        from orders.services.tax_engine import GST, Line, compute
        lines = [Line(amount=D("600"), rate=D("5"), kind=GST)]
        below = compute(lines, discount_type="amount", discount_value=D("100"), discount_min_order=D("601"))
        at = compute(lines, discount_type="amount", discount_value=D("100"), discount_min_order=D("600"))
        self.assertEqual((below.discount, below.order_discount), (D("0.00"), D("0.00")))
        self.assertEqual((at.discount, at.order_discount), (D("100.00"), D("100.00")))


# ── Root A: one owner of the bill's discount ─────────────────────────

class OneOwnerOfTheDiscountTest(PromoBase):

    def test_an_order_sent_with_no_discount_takes_the_promo_off(self):
        """P2."""
        promo = self.promo()
        self.discount(promo_id=promo.id)
        resp = self.post("cashier", reverse("create-order"), {
            "order_id": self.order.id, "source": "takeaway", "cart": [{"id": self.pitcher.id, "quantity": 1}],
            "discount_type": "percentage", "discount_value": 0})
        self.assertEqual(resp.status_code, 200, resp.content)
        order = self.fresh()
        self.assertEqual((order.promo, order.promo_name, order.discount_value), (None, "", D("0.00")))
        self.assertEqual(promo.uses, 0)

    def test_the_database_refuses_a_promo_with_no_discount(self):
        self.discount(promo_id=self.promo().id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.filter(pk=self.order.pk).update(discount_value=D("0"))

    def test_the_model_helpers_follow_the_same_rules(self):
        promo = self.promo()
        self.discount(promo_id=promo.id)
        order = self.fresh()
        order.apply_discount("percentage", D("5"))
        order = self.fresh()
        self.assertEqual((order.promo, order.promo_name, order.discount_value), (None, "", D("5.00")))
        self.discount(promo_id=promo.id)
        order = self.fresh()
        order.clear_discount()
        order = self.fresh()
        self.assertEqual((order.promo, order.discount_value, order.discount_total), (None, D("0.00"), D("0.00")))

    def test_reapplying_an_edited_promo_takes_its_new_values(self):
        promo = self.promo(value="10")
        self.discount(promo_id=promo.id)
        Promo.objects.filter(pk=promo.pk).update(discount_value=D("15"), name="Happier Hour")
        self.discount(promo_id=promo.id)
        order = self.fresh()
        self.assertEqual((order.discount_value, order.promo_name), (D("15.00"), "Happier Hour"))
        self.assertEqual(promo.uses, 1)


# ── Root C: uses are counted from the bills ──────────────────────────

class UsesFromBillsTest(PromoBase):

    def test_a_promo_used_by_any_path_is_counted(self):
        promo = self.promo()
        second = self.new_order()
        for order in (self.order, second):
            Order.objects.filter(pk=order.pk).update(promo=promo, promo_name=promo.name,
                                                     discount_type="percentage", discount_value=D("10"))
        self.assertEqual(promo.uses, 2)

    def test_a_cancelled_bill_does_not_count(self):
        promo = self.promo(max_uses=1)
        self.discount(promo_id=promo.id)
        cancel_whole_order(self.users["manager"], self.order.id, reason="Guest left")
        self.assertEqual(promo.uses, 0)
        self.assertEqual(self.discount(order=self.new_order(), promo_id=promo.id).status_code, 200)


# ── P3: the audit report counts bills ────────────────────────────────

class AuditCountsBillsTest(PromoBase):

    def report(self):
        day = self.today()
        return discount_void_audit(self.tenant, self.outlet, day, day)["promos_used"]

    def test_a_promo_taken_off_again_is_not_counted(self):
        self.discount(promo_id=self.promo("Happy Hour").id)
        self.discount(type="percentage", value=0)
        self.assertEqual(self.report(), [])

    def test_paid_bills_are_counted_with_what_the_promo_gave(self):
        promo = self.promo("Happy Hour")
        for order in (self.order, self.new_order()):
            self.discount(order=order, promo_id=promo.id)
            Order.objects.filter(pk=order.pk).update(status="paid")
        open_one = self.new_order()
        self.discount(order=open_one, promo_id=promo.id)            # not paid: not counted yet
        self.assertEqual(self.report(), [{"promo_name": "Happy Hour", "bills": 2, "given": D("200.00")}])


# ── P7: the bill names the promo ─────────────────────────────────────

class BillNamesThePromoTest(PromoBase):

    def rows(self):
        order = Order.objects.prefetch_related("items").get(pk=self.order.pk)
        layout = bill_layout(order)
        self.assertEqual(sum((r.amount for r in layout.rows), D("0")), layout.total)    # still adds up
        return [(r.key, r.label, r.amount) for r in layout.rows]

    def test_the_discount_row_says_which_promo(self):
        self.discount(promo_id=self.promo("Happy Hour", code="HH10").id)
        self.assertIn(("promo", "Promo HH10", D("-100.00")), self.rows())
        self.assertNotIn("discount", [key for key, _, _ in self.rows()])

    def test_without_a_code_the_name_is_shown_cut_to_fit(self):
        self.discount(promo_id=self.promo("A very long weekend celebration offer").id)
        labels = [label for key, label, _ in self.rows() if key == "promo"]
        self.assertEqual(len(labels), 1)
        self.assertTrue(labels[0].startswith("Promo A very long"))
        self.assertLessEqual(len(labels[0]), 24)

    def test_a_dish_discount_and_a_promo_are_two_rows(self):
        self.add("500")
        self.discount(promo_id=self.promo("Happy Hour", code="HH10").id)      # 10% of 1500 = 150
        item = self.order.items.get(price=D("500"))
        resp = self.post("manager", reverse("item-discount", args=[item.id]), {"percent": 20, "reason": "cold"})
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = self.rows()
        self.assertIn(("discount", "Discount", D("-100.00")), rows)                 # 20% of 500
        self.assertIn(("promo", "Promo HH10", D("-140.00")), rows)                  # 10% of 1400

    def test_a_paused_promo_is_not_on_the_bill(self):
        self.add("200")
        self.discount(promo_id=self.flat(code="BIG").id)
        void_order_item(self.users["manager"], self.order.items.get(price=D("1000")).id, "wrong dish")
        self.assertEqual([key for key, _, _ in self.rows()], ["subtotal"])

    def test_the_bill_screen_names_the_promo_and_says_when_it_is_paused(self):
        self.add("200")
        self.discount(promo_id=self.flat(name="Big Spender").id)
        client = Client()
        client.force_login(self.users["cashier"])
        page = client.get(reverse("bill-view", args=[self.order.id])).content.decode()
        self.assertIn("Big Spender", page)
        void_order_item(self.users["manager"], self.order.items.get(price=D("1000")).id, "wrong dish")
        page = client.get(reverse("bill-view", args=[self.order.id])).content.decode()
        self.assertIn("needs a bill of ₹1,000", page)


# ── P4, P5, D2, D3, D4: the Setup screen ─────────────────────────────

class SetupStatusTest(PromoBase):
    """P4."""

    def test_each_promo_says_what_it_really_is(self):
        day = self.today()
        cases = {
            "Now": {},
            "Later": {"valid_from": day + timedelta(days=7)},
            "Gone": {"valid_until": day - timedelta(days=1)},
            "Off": {"is_active": False},
            "Full": {"max_uses": 1},
        }
        promos = {name: self.promo(name, **fields) for name, fields in cases.items()}
        self.discount(promo_id=promos["Full"].id)
        texts = {name: p.status_for(self.outlet)[1] for name, p in promos.items()}
        self.assertEqual(texts["Now"], "Active")
        self.assertEqual(texts["Later"], f"Starts {(day + timedelta(days=7)):%d %b}")
        self.assertEqual(texts["Gone"], f"Ended {(day - timedelta(days=1)):%d %b}")
        self.assertEqual(texts["Off"], "Paused")
        self.assertEqual(texts["Full"], "Used up")
        client = Client()
        client.force_login(self.users["owner"])
        page = client.get(reverse("setup_promos")).content.decode()
        self.assertIn("Used up", page)
        self.assertIn("Starts ", page)
        self.assertNotIn("Expired", page)


class SetupPermissionsTest(PromoBase):
    """P5 and D2: owner: every promo; manager: only their own outlet's."""

    def setUp(self):
        super().setUp()
        self.other = Outlet.objects.create(tenant=self.tenant, name="Other")

    def form(self, **extra):
        return {"name": "Fifty", "discount_type": "percentage", "discount_value": "50", **extra}

    def test_an_archived_promo_cannot_be_switched_on(self):
        promo = self.promo()
        self.post("owner", reverse("promo_delete", args=[promo.id]), {})
        resp = self.post("owner", reverse("promo_toggle", args=[promo.id]), {})
        self.assertEqual(resp.status_code, 404)
        promo.refresh_from_db()
        self.assertFalse(promo.is_active)

    def test_a_manager_cannot_make_a_promo_elsewhere_or_everywhere(self):
        elsewhere = self.post("manager", reverse("promo_create"), self.form(outlet_id=self.other.id))
        everywhere = self.post("manager", reverse("promo_create"), self.form(all_outlets=True))
        self.assertEqual((elsewhere.status_code, everywhere.status_code), (403, 403))
        self.assertFalse(Promo.objects.filter(name="Fifty").exists())

    def test_a_manager_makes_promos_for_their_own_outlet(self):
        resp = self.post("manager", reverse("promo_create"), self.form())
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Promo.objects.get(name="Fifty").outlet, self.outlet)

    def test_a_manager_cannot_change_an_all_outlet_promo(self):
        promo = self.promo()                                   # outlet=None: every outlet
        for name in ("promo_toggle", "promo_delete", "promo_update"):
            resp = self.post("manager", reverse(name, args=[promo.id]), self.form())
            self.assertEqual(resp.status_code, 403, name)
        promo.refresh_from_db()
        self.assertEqual((promo.is_active, promo.archived_at, promo.name), (True, None, "Happy Hour"))

    def test_the_owner_makes_promos_anywhere(self):
        for extra in ({"outlet_id": self.other.id}, {"all_outlets": True}):
            resp = self.post("owner", reverse("promo_create"), self.form(**extra, name=f"Owner {len(extra)}"))
            self.assertEqual(resp.status_code, 200, resp.content)


class FlatPromoMinimumTest(PromoBase):
    """D3: a flat promo can never make a bill free."""

    def test_a_flat_promo_needs_a_minimum_at_least_as_big(self):
        resp = self.post("owner", reverse("promo_create"), {
            "name": "Hundred off", "discount_type": "amount", "discount_value": "100", "min_order_value": "60"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("minimum order of at least ₹100", resp.json()["error"])
        resp = self.post("owner", reverse("promo_create"), {
            "name": "Hundred off", "discount_type": "amount", "discount_value": "100", "min_order_value": "100"})
        self.assertEqual(resp.status_code, 200, resp.content)


class EditPromoTest(PromoBase):
    """D4."""

    def edit(self, promo, role="owner", **fields):
        body = {"name": promo.name, "code": promo.code, "discount_type": promo.discount_type,
                "discount_value": str(promo.discount_value), "min_order_value": str(promo.min_order_value),
                **fields}
        return self.post(role, reverse("promo_update", args=[promo.id]), body)

    def test_the_owner_edits_a_promo_and_bills_keep_what_they_got(self):
        promo = self.promo("Happy Hour", value="10")
        self.discount(promo_id=promo.id)
        resp = self.edit(promo, name="Happier Hour", discount_value="15", max_uses=50)
        self.assertEqual(resp.status_code, 200, resp.content)
        promo.refresh_from_db()
        self.assertEqual((promo.name, promo.discount_value, promo.max_uses), ("Happier Hour", D("15.00"), 50))
        order = self.fresh()
        self.assertEqual((order.promo_name, order.discount_value, order.discount_total),
                         ("Happy Hour", D("10.00"), D("100.00")))

    def test_an_edit_is_checked_like_a_new_promo(self):
        promo = self.promo()
        self.assertEqual(self.edit(promo, discount_value="150").status_code, 400)       # > 100%
        self.promo("Taken", code="TAKEN")
        self.assertEqual(self.edit(promo, code="TAKEN").status_code, 409)

    def test_an_archived_promo_cannot_be_edited(self):
        promo = self.promo()
        self.post("owner", reverse("promo_delete", args=[promo.id]), {})
        self.assertEqual(self.edit(promo, name="Back").status_code, 404)

    def test_a_manager_edits_their_own_outlets_promo(self):
        promo = self.promo(outlet=self.outlet)
        self.assertEqual(self.edit(promo, role="manager", name="Mine").status_code, 200)
        promo.refresh_from_db()
        self.assertEqual(promo.name, "Mine")


# ── Two tills, one last use ──────────────────────────────────────────

@skipUnless(connection.vendor == "postgresql", "row locks are tested on Postgres")
class LastUseRaceTest(TransactionTestCase):
    """Eight tills apply a promo with 3 uses left at the same moment, each
    to its own bill: exactly 3 get it. The cap check counts bills under the
    promo's row lock (Promo.locked), so a till waits for the one before it
    and then sees its bill."""

    def setUp(self):
        Base.setUp(self)

    new_order = Base.new_order

    def test_only_the_uses_left_are_given(self):
        promo = Promo.objects.create(tenant=self.tenant, name="Three only", discount_type="percentage",
                                     discount_value=D("10"), max_uses=3)
        orders = [self.new_order() for _ in range(8)]
        start = threading.Barrier(len(orders))
        outcomes, errors = [], []

        def till(order_id):
            try:
                start.wait()
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(pk=order_id)
                    set_promo(order, promo)
                    order.recalculate_totals()
                outcomes.append("got it")
            except PromoRefused:
                outcomes.append("refused")
            except Exception as exc:          # reported below, not lost in the thread
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=till, args=(order.pk,)) for order in orders]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(outcomes), ["got it"] * 3 + ["refused"] * 5)
        self.assertEqual(promo.uses, 3)
