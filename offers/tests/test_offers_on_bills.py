"""
Offers on real bills: offers/services.py run by Order.recalculate_totals,
through the tax engine, for the awkward cases in the design (section 8 of
md_files/rasova_pub_offers_design_and_promo_review_2026-10-03.html).

Run: python manage.py test offers.tests.test_offers_on_bills
"""
from datetime import datetime, time, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from django.db.models import RestrictedError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from menu.liquor import add_liquor_class
from menu.models import MenuCategory, MenuItem
from offers.models import Offer, OfferTarget, OfferWindow
from orders.exceptions import IssuedBillError
from orders.models import Order, OrderItem
from orders.services.order_service import add_items_to_order
from orders.services.void_service import void_order_item
from tenants.models import Outlet, Tenant, TenantFeatureOverride

IST = ZoneInfo("Asia/Kolkata")
KARNATAKA_GSTIN = "29ABCDE1234F1Z5"


def at(day, hour, minute=0):
    """October 2026: 2 Oct is a Friday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


class PubBills(TestCase):
    tenant_type = "pub"

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Copper Tap", tenant_type=self.tenant_type)
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Indiranagar", gst_no=KARNATAKA_GSTIN)
        self.owner = User.objects.create_user(username="tap_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        self.food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        beer = add_liquor_class(self.outlet, "Beer")                  # Karnataka: 0%
        self.pitcher = self.dish("Lager Pitcher", "2300", self.bar, vat_class=beer)
        self.premium = self.dish("Craft Pitcher", "2500", self.bar, vat_class=beer)
        self.paneer = self.dish("Paneer Tikka", "220", self.food, gst="5")

    def dish(self, name, price, category, gst="0", vat_class=None):
        return MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category, name=name,
                                       price=D(price), gst_percentage=D(gst), vat_class=vat_class)

    def offer(self, name="Buy 2 pitchers, get 1 free", kind="buy_get_free", outlet="same", targets=(),
              windows=(), **fields):
        if kind == "buy_get_free":
            fields.setdefault("buy_qty", 2)
        offer = Offer.objects.create(tenant=self.tenant, outlet=self.outlet if outlet == "same" else outlet,
                                     name=name, kind=kind, **fields)
        for target in targets:
            OfferTarget.objects.create(offer=offer, **{
                "category" if isinstance(target, MenuCategory) else "menu_item": target})
        for days, start, end in windows:
            OfferWindow.objects.create(offer=offer, days=days, start_time=start, end_time=end)
        return offer

    def bill(self, *cart, source="dine_in", added=None):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
                                     source=source, status="open")
        self.add(order, *cart, added=added)
        return order

    def add(self, order, *cart, added=None):
        before = set(order.items.values_list("id", flat=True))
        add_items_to_order(self.owner, order, [{"id": dish.id, "quantity": qty} for dish, qty in cart])
        if added is not None:
            order.items.exclude(id__in=before).update(added_at=added)
            Order.objects.get(pk=order.pk).recalculate_totals()
        order.refresh_from_db()
        return order

    def line(self, order, dish):
        return order.items.get(menu_item=dish, status__in=["pending", "sent", "served"])


class TheWorkedBillTest(PubBills):
    def test_three_pitchers_and_two_paneer(self):
        # The design page's bill: 2,300 + 2,500 + 2,300 under "buy 2, get the
        # cheapest of every 3 free", with two Paneer Tikka at 5% GST.
        self.offer(targets=[self.bar])
        order = self.bill((self.pitcher, 1), (self.premium, 1), (self.pitcher, 1), (self.paneer, 2))
        lines = list(order.items.order_by("id"))
        self.assertEqual([line.offer_discount for line in lines], [D("0"), D("0"), D("2300"), D("0")])
        self.assertEqual(lines[2].offer_name, "Buy 2 pitchers, get 1 free")
        self.assertEqual(order.offer_total, D("2300.00"))
        self.assertEqual(order.discount_total, D("2300.00"))
        self.assertEqual(order.gst_total, D("22.00"))           # the food's GST is untouched
        self.assertEqual(order.vat_total, D("0.00"))
        self.assertEqual(order.grand_total, D("5262"))

    def test_without_the_feature_nothing_happens(self):
        self.tenant.tenant_type = "fine_dining"
        self.tenant.save()
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="liquor_vat", enabled=True)
        self.offer(targets=[self.bar])
        order = self.bill((self.pitcher, 3))
        self.assertEqual((order.offer_total, order.grand_total), (D("0.00"), D("6900")))

    def test_another_outlets_offer_never_applies_and_an_all_outlets_one_does(self):
        elsewhere = Outlet.objects.create(tenant=self.tenant, name="Koramangala")
        self.offer(outlet=elsewhere, targets=[self.bar])
        self.assertEqual(self.bill((self.pitcher, 3)).offer_total, D("0.00"))
        self.offer(name="Everywhere", outlet=None, targets=[self.bar])
        self.assertEqual(self.bill((self.pitcher, 3)).offer_total, D("2300.00"))


class ChangesReCheckTheOfferTest(PubBills):
    def test_voiding_a_paid_pitcher_withdraws_the_free_one(self):
        self.offer(targets=[self.bar])
        order = self.bill((self.pitcher, 1), (self.premium, 1), (self.pitcher, 1))
        free = order.items.order_by("id").last()
        self.assertEqual(free.offer_discount, D("2300.00"))
        void_order_item(self.owner, self.line(order, self.premium).id, "Guest changed mind")
        order.refresh_from_db()
        free.refresh_from_db()
        self.assertEqual((free.offer_discount, free.offer_name, free.offer_id), (D("0.00"), "", None))
        self.assertEqual((order.offer_total, order.grand_total), (D("0.00"), D("4600")))

    def test_voiding_the_free_pitcher_leaves_no_offer_on_it(self):
        self.offer(targets=[self.bar])
        order = self.bill((self.pitcher, 1), (self.premium, 1), (self.pitcher, 1))
        free = order.items.order_by("id").last()
        void_order_item(self.owner, free.id, "Spilled")
        free.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((free.status, free.offer_discount), ("voided", D("0.00")))
        self.assertEqual((order.offer_total, order.grand_total), (D("0.00"), D("4800")))

    def test_a_fourth_round_completes_the_next_group(self):
        self.offer(targets=[self.bar])
        order = self.bill((self.pitcher, 2))
        self.assertEqual(order.offer_total, D("0.00"))
        order = self.add(order, (self.pitcher, 1))
        self.assertEqual(order.offer_total, D("2300.00"))
        order = self.add(order, (self.pitcher, 2))
        self.assertEqual(order.offer_total, D("2300.00"))     # five pitchers: one group
        order = self.add(order, (self.pitcher, 1))
        self.assertEqual(order.offer_total, D("4600.00"))     # six: two groups

    def test_a_staff_dish_discount_and_an_offer_never_stack(self):
        self.offer(targets=[self.bar])
        order = self.bill((self.pitcher, 3))
        line = order.items.get()
        line.item_discount_pct = D("10")
        line.save(update_fields=["item_discount_pct"])
        order.recalculate_totals()
        line.refresh_from_db()
        self.assertEqual((line.offer_discount, order.offer_total), (D("0.00"), D("0.00")))
        self.assertEqual(order.grand_total, D("6210"))          # 6,900 less 10%

    def test_a_free_dish_neither_gets_nor_completes_an_offer(self):
        self.offer(targets=[self.bar])
        order = self.bill((self.pitcher, 2), (self.pitcher, 1))
        comp = order.items.order_by("id").last()
        comp.is_complimentary = True
        comp.save(update_fields=["is_complimentary"])
        order.recalculate_totals()
        self.assertEqual((order.offer_total, order.grand_total), (D("0.00"), D("4600")))

    def test_a_bill_discount_comes_after_the_offer(self):
        self.offer(targets=[self.bar])
        order = self.bill((self.pitcher, 3), (self.paneer, 2))
        order.apply_discount("percentage", D("10"))
        order.refresh_from_db()
        # Offer 2,300 off 7,340, then 10% of the 5,040 left = 504.
        self.assertEqual(order.offer_total, D("2300.00"))
        self.assertEqual(order.discount_total, D("2804.00"))


class ThePriceIsLockedTest(PubBills):
    def happy_hour(self, **kw):
        return self.offer(name="Happy hour 25%", kind="percent_off", percent=D("25"), targets=[self.bar],
                          windows=[("01234", time(17), time(20))], **kw)

    def test_ordered_in_happy_hour_keeps_it_after(self):
        self.happy_hour()
        order = self.bill((self.pitcher, 1), added=at(2, 19, 55))
        self.assertEqual(order.offer_total, D("575.00"))
        order = self.add(order, (self.pitcher, 1), added=at(2, 20, 5))   # after 8: full price
        self.assertEqual(order.offer_total, D("575.00"))
        self.assertEqual(order.grand_total, D("4025"))

    def test_a_friday_window_past_midnight(self):
        self.offer(name="Friday late 10%", kind="percent_off", percent=D("10"), targets=[self.bar],
                   windows=[("4", time(20), time(2))])
        order = self.bill((self.pitcher, 1), added=at(3, 1))                # Saturday 1 AM
        self.assertEqual(order.offer_total, D("230.00"))
        order = self.add(order, (self.pitcher, 1), added=at(3, 2, 30))      # after 2
        self.assertEqual(order.offer_total, D("230.00"))

    def test_switching_an_offer_off_keeps_what_was_already_ordered(self):
        offer = self.offer(kind="percent_off", name="Bar 10%", percent=D("10"), targets=[self.bar])
        order = self.bill((self.pitcher, 1), added=timezone.now() - timedelta(minutes=30))
        offer.is_active = False
        offer.save()
        self.assertIsNotNone(offer.paused_at)
        order = self.add(order, (self.pitcher, 1))                           # added after
        self.assertEqual(order.offer_total, D("230.00"))
        offer.is_active = True
        offer.save()
        self.assertIsNone(offer.paused_at)

    def test_an_old_line_without_a_time_uses_the_orders(self):
        self.happy_hour()
        order = self.bill((self.pitcher, 1))
        Order.objects.filter(pk=order.pk).update(created_at=at(2, 18))
        order.items.update(added_at=None)
        Order.objects.get(pk=order.pk).recalculate_totals()
        order.refresh_from_db()
        self.assertEqual(order.offer_total, D("575.00"))

    def test_a_new_line_records_when_it_was_added(self):
        before = timezone.now()
        order = self.bill((self.pitcher, 1))
        added = order.items.get().added_at
        self.assertTrue(before <= added <= timezone.now())


class WhereOffersNeverGoTest(PubBills):
    def test_no_offer_on_liquor_in_a_parcel(self):
        self.offer(targets=[self.bar])
        self.offer(name="Food 10%", kind="percent_off", percent=D("10"), targets=[self.food])
        order = self.bill((self.pitcher, 3), (self.paneer, 2), source="takeaway")
        self.assertEqual(self.line(order, self.pitcher).offer_discount, D("0.00"))
        self.assertEqual(self.line(order, self.paneer).offer_discount, D("44.00"))   # food still gets its own
        self.assertEqual(order.offer_total, D("44.00"))

    def test_no_offers_on_a_zomato_order(self):
        self.offer(targets=[self.bar])
        self.offer(name="Food 10%", kind="percent_off", percent=D("10"), targets=[self.food])
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, source="zomato", status="open")
        OrderItem.objects.create(order=order, menu_item=self.paneer, quantity=2, price=D("220"),
                                 gst_percentage=D("5"), total_price=D("440"))
        order.recalculate_totals()
        self.assertEqual(order.offer_total, D("0.00"))

    def test_a_qr_guest_gets_the_offer_from_the_server(self):
        # A guest (no login) orders through the QR menu: the server works the
        # offer out, never the guest's screen.
        self.offer(targets=[self.bar])
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, source="dine_in", status="open")
        add_items_to_order(None, order, [{"id": self.pitcher.id, "quantity": 3}],
                           tenant=self.tenant, outlet=self.outlet)
        order.refresh_from_db()
        self.assertEqual(order.offer_total, D("2300.00"))


class IssuedBillsNeverChangeTest(PubBills):
    def test_a_paid_bill_keeps_its_offer_and_the_offer_cannot_be_deleted(self):
        offer = self.offer(targets=[self.bar])
        order = self.bill((self.pitcher, 3))
        order.status = "paid"
        order.save()
        offer.name = "Renamed"
        offer.save()
        offer.archive()
        order.refresh_from_db()
        line = order.items.get()
        self.assertEqual((line.offer_name, line.offer_discount), ("Buy 2 pitchers, get 1 free", D("2300.00")))
        self.assertEqual(order.grand_total, D("4600"))
        with self.assertRaises(IssuedBillError):
            order.recalculate_totals()
        with self.assertRaises(RestrictedError):
            offer.delete()

    def test_a_restaurant_can_still_be_deleted_with_its_offers_and_bills(self):
        self.offer(targets=[self.bar])
        self.bill((self.pitcher, 3))
        # A liquor class in use still can't go on its own (menu.MenuItem.vat_class)...
        with self.assertRaises(RestrictedError):
            self.pitcher.vat_class.delete()
        # ...but the whole restaurant can: PROTECT used to refuse any pub with liquor.
        tenant_id = self.tenant.pk
        self.tenant.delete()
        self.assertFalse(Offer.objects.filter(tenant_id=tenant_id).exists())


class OfferRulesTest(PubBills):
    def test_an_offer_checks_its_own_numbers(self):
        from django.core.exceptions import ValidationError
        bad = [
            dict(kind="buy_get_free", buy_qty=0),
            dict(kind="buy_get_free", buy_qty=15, free_qty=10),
            dict(kind="percent_off", percent=D("0")),
            dict(kind="percent_off", percent=D("120")),
            dict(kind="amount_off", amount=None),
        ]
        for fields in bad:
            with self.assertRaises(ValidationError, msg=fields):
                Offer(tenant=self.tenant, name="x", **fields).full_clean()
        window = OfferWindow(offer=self.offer(), days="78", start_time=time(20), end_time=time(20))
        with self.assertRaises(ValidationError):
            window.full_clean()


class OffersOnTheBillTest(PubBills):
    """What the guest and the owner see."""

    def setUp(self):
        super().setUp()
        self.offer(targets=[self.bar])

    def test_the_bill_shows_offers_apart_from_discounts(self):
        from orders.services.bill_layout import bill_layout
        order = self.bill((self.pitcher, 3), (self.paneer, 2))
        layout = bill_layout(order)
        rows = {row.key: row.amount for row in layout.rows}
        self.assertEqual(rows["offers"], D("-2300.00"))
        self.assertNotIn("discount", rows)
        self.assertEqual(sum(row.amount for row in layout.rows), layout.total)
        pitcher_line = layout.lines[0]
        self.assertEqual(pitcher_line.offer_text, "Buy 2 pitchers, get 1 free  -2300.00")
        self.assertEqual(layout.lines[1].offer_text, "")

        order.apply_discount("percentage", D("10"))
        order.refresh_from_db()
        rows = {row.key: row.amount for row in bill_layout(order).rows}
        self.assertEqual((rows["offers"], rows["discount"]), (D("-2300.00"), D("-504.00")))

    def test_every_bill_format_names_the_offer(self):
        from orders.services.bill_layout import bill_layout
        from printing.services.printing_service import PrintingService
        order = self.bill((self.pitcher, 3))
        line = bill_layout(order).lines[0]
        rows = PrintingService(printer_type="console", chars_per_line=48)._dish_rows(line, 48)
        self.assertTrue(rows[-1].rstrip().endswith("-2300.00"))
        self.assertIn("Buy 2 pitchers, get 1 free", rows[-1])
        self.assertTrue(all(len(row) <= 48 for row in rows))

        from django.test import Client
        from django.urls import reverse
        client = Client()
        client.force_login(self.owner)
        for name in ("bill-view", "thermal-receipt"):
            page = client.get(reverse(name, args=[order.id])).content.decode()
            self.assertIn("Buy 2 pitchers, get 1 free", page, name)
            self.assertIn("Offers", page, name)

    def test_the_day_end_report_counts_billed_value(self):
        from django.test import Client
        from django.urls import reverse
        order = self.bill((self.pitcher, 3), (self.paneer, 2))
        void_order_item(self.owner, self.line(order, self.paneer).id, "Wrong table")
        self.add(order, (self.paneer, 1))
        comp = order.items.order_by("id").last()
        comp.is_complimentary = True
        comp.save(update_fields=["is_complimentary"])
        order.recalculate_totals()
        Order.objects.filter(pk=order.pk).update(status="paid")
        client = Client()
        client.force_login(self.owner)
        csv_text = client.get(reverse("export-z-report")).content.decode()
        rows = {line.split(",")[0]: line.split(",")[1:] for line in csv_text.splitlines() if line}
        self.assertEqual(rows["  of which Offers"], ["2300.00"])
        # 3 pitchers billed 4,600 after the offer; the voided paneer isn't a
        # sale and the free one is 0.
        self.assertEqual(rows["Lager Pitcher"], ["3", "4600.00"])
        self.assertEqual(rows["Paneer Tikka"], ["1", "0.00"])


class CartPagesTest(PubBills):
    """The carts get the offers a new line can take, in the form
    static/js/offers.js reads; a restaurant without offers gets nothing."""

    def page(self, url):
        from django.test import Client
        client = Client()
        client.force_login(self.owner)
        return client.get(url).content.decode()

    def test_the_pos_cart_carries_the_offers(self):
        import json
        import re
        from django.urls import reverse
        self.offer(targets=[self.bar])
        paused = self.offer(name="Paused", kind="percent_off", percent=D("5"))
        paused.is_active = False
        paused.save()
        html = self.page(reverse("billing-view"))
        self.assertIn("js/offers.js", html)
        data = json.loads(re.search(r'<script id="page-offers" type="application/json">(.*?)</script>',
                                    html, re.S).group(1))
        self.assertEqual([rule["name"] for rule in data["rules"]], ["Buy 2 pitchers, get 1 free"])
        self.assertEqual(data["rules"][0]["categories"], [self.bar.id])
        self.assertEqual(data["dishes"][str(self.pitcher.id)], {"category": self.bar.id, "price": "2300.00"})
        self.assertEqual((data["cutoffHour"], data["utcOffset"]), (6, 330))

    def test_no_offers_no_payload(self):
        from offers.services import page_offers
        self.assertIsNone(page_offers(self.tenant, self.outlet))       # none set up
        self.offer(targets=[self.bar])
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="offers", enabled=False)
        self.tenant = Tenant.objects.get(pk=self.tenant.pk)
        self.assertIsNone(page_offers(self.tenant, self.outlet))       # feature off


class ModifiersAreNeverDiscountedTest(PubBills):
    def test_a_free_whisky_is_the_whisky_not_its_mixer(self):
        # Two lines of whisky at 400 with a 100 mixer each (the line's
        # total_price carries the mixer). 1 + 1: the free one is 400, and the
        # guest still pays both mixers.
        self.offer(name="Whisky 1+1", buy_qty=1, targets=[self.bar])
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, source="dine_in", status="open")
        whisky = self.dish("Whisky 60 ml", "400", self.bar)
        for _ in range(2):
            OrderItem.objects.create(order=order, menu_item=whisky, quantity=1, price=D("400"),
                                     gst_percentage=D("0"), total_price=D("500"))
        order.recalculate_totals()
        self.assertEqual(order.offer_total, D("400.00"))
        self.assertEqual(order.grand_total, D("600"))
