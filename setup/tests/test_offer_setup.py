"""
Setup, then Offers (setup/views/offer_views.py, setup_offers.html): the
owner or a manager sets up an offer, every number and choice checked, and
the live example bill on the page prices it with the real menu.

Run: python manage.py test setup.tests.test_offer_setup
"""
import html
import json
import re
import subprocess
from decimal import Decimal as D

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from menu.liquor import add_liquor_class
from menu.models import MenuCategory, MenuItem
from menu.tests.test_qr_cart_js import HARNESS, STATIC_JS
from offers.models import Offer
from orders.models import Order
from orders.services.order_service import add_items_to_order
from orders.tests.test_cart_tax_js import NODE, _needs_node
from tenants.models import Outlet, Tenant, TenantFeatureOverride


class OfferSetupBase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Copper Tap", tenant_type="pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Indiranagar", gst_no="29ABCDE1234F1Z5")
        self.other_outlet = Outlet.objects.create(tenant=self.tenant, name="Koramangala")
        self.owner = self.user("ot_owner", "owner")
        self.manager = self.user("ot_manager", "manager")
        self.cashier = self.user("ot_cashier", "cashier")
        self.bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        beer = add_liquor_class(self.outlet, "Beer")
        self.lager = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=self.bar,
                                             name="Lager Pitcher", price=D("2300"), gst_percentage=D("0"),
                                             vat_class=beer)
        self.craft = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=self.bar,
                                             name="Craft Pitcher", price=D("2500"), gst_percentage=D("0"),
                                             vat_class=beer)
        other_cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.other_outlet, name="Bar")
        self.elsewhere = MenuItem.objects.create(tenant=self.tenant, outlet=self.other_outlet,
                                                 category=other_cat, name="Far Pitcher", price=D("2000"),
                                                 gst_percentage=D("0"))

    def user(self, username, role, outlet=None):
        return User.objects.create_user(username=username, password="pw", role=role, tenant=self.tenant,
                                        outlet=outlet or self.outlet)

    def post(self, user, name, body=None, *args):
        client = Client()
        client.force_login(user)
        return client.post(reverse(name, args=args), data=json.dumps(body or {}),
                           content_type="application/json")

    def create(self, user=None, **fields):
        body = {"template": "buy_get_free", "name": "Buy 2 pitchers, get 1 free", "buy_qty": 2, "free_qty": 1}
        body.update(fields)
        return self.post(user or self.owner, "offer_create", body)


class CreateTest(OfferSetupBase):
    def test_buy_two_get_one_on_the_bar_then_a_bill_uses_it(self):
        resp = self.create(category_ids=[self.bar.id])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["summary"], "Buy 2, get 1 free")
        offer = Offer.objects.get()
        self.assertEqual((offer.outlet, offer.created_by, offer.kind), (self.outlet, self.owner, "buy_get_free"))
        self.assertEqual([t.category_id for t in offer.targets.all()], [self.bar.id])
        self.assertFalse(offer.windows.exists())

        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, source="dine_in", status="open")
        add_items_to_order(self.owner, order, [{"id": self.lager.id, "quantity": 2}, {"id": self.craft.id, "quantity": 1}])
        order.refresh_from_db()
        self.assertEqual((order.offer_total, order.grand_total), (D("2300.00"), D("4800")))

    def test_a_happy_hour_keeps_its_days_and_hours(self):
        resp = self.create(template="happy_hour", name="Happy hour 25%", percent="25",
                           dish_ids=[self.lager.id], days=[0, 1, 2, 3, 4], start_time="17:00", end_time="20:00")
        self.assertEqual(resp.status_code, 200, resp.content)
        offer = Offer.objects.get()
        window = offer.windows.get()
        self.assertEqual((offer.kind, offer.percent), ("percent_off", D("25.00")))
        self.assertEqual((window.days, str(window.start_time), str(window.end_time)), ("01234", "17:00:00", "20:00:00"))
        self.assertEqual([t.menu_item_id for t in offer.targets.all()], [self.lager.id])

    def test_every_mistake_is_explained_and_nothing_is_saved(self):
        cases = [
            ({"template": "bogus"}, "Pick what kind of offer this is."),
            ({"name": "  "}, "Give the offer a name, as the bill will show it."),
            ({"name": "x" * 81}, "The name can be at most 80 characters."),
            ({"buy_qty": 0}, "Buy must be from 1 to 19."),
            ({"buy_qty": "two"}, "Buy must be a whole number."),
            ({"buy_qty": 15, "free_qty": 10}, "Buy and free together can be at most 20."),
            ({"template": "percent_off", "percent": "0"}, "The percent off must be more than 0."),
            ({"template": "percent_off", "percent": "150"}, "The percent off can't be more than 100."),
            ({"template": "percent_off", "percent": "NaN"}, "The percent off must be a number."),
            ({"template": "amount_off", "amount": "-5"}, "The amount off can't be negative."),
            ({"template": "happy_hour", "percent": "25"}, "A happy hour needs its hours."),
            ({"start_time": "20:00", "end_time": "20:00"}, "The end can't be the same as the start."),
            ({"start_time": "8pm"}, "The start must be a time like 17:00."),
            ({"days": [7]}, "Days: pick from the list."),
            ({"days": ["mon"]}, "Days: pick from the list."),
            ({"valid_from": "2026-10-10", "valid_until": "2026-10-01"}, "The last day can't be before the first day."),
            ({"dish_ids": [self.elsewhere.id]}, "A dish or category isn't on this outlet's menu."),
            ({"category_ids": [999999]}, "A dish or category isn't on this outlet's menu."),
            ({"all_outlets": True, "dish_ids": [self.lager.id]},
             "An offer for every outlet covers the whole menu. To pick dishes, make it for one outlet."),
            ({"priority": 500}, "Priority must be from -100 to 100."),
        ]
        for fields, message in cases:
            resp = self.create(**fields)
            self.assertEqual(resp.status_code, 400, fields)
            self.assertEqual(resp.json()["error"], message, fields)
        self.assertFalse(Offer.objects.exists())

    def test_an_owner_can_run_one_at_every_outlet(self):
        resp = self.create(all_outlets=True, name="House 1+1", buy_qty=1)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIsNone(Offer.objects.get().outlet)


class WhoMayTest(OfferSetupBase):
    def test_a_cashier_may_not(self):
        self.assertEqual(self.create(user=self.cashier).status_code, 403)
        client = Client()
        client.force_login(self.cashier)
        self.assertEqual(client.get(reverse("setup_offers")).status_code, 302)

    def test_a_manager_sets_offers_for_their_own_outlet_only(self):
        resp = self.create(user=self.manager, all_outlets=True)
        self.assertEqual(resp.json()["error"], "Only the owner can run an offer at every outlet.")
        resp = self.create(user=self.manager, outlet_id=self.other_outlet.id)
        self.assertEqual(resp.json()["error"], "A manager sets offers for their own outlet.")
        far = Offer.objects.create(tenant=self.tenant, outlet=self.other_outlet, name="Far", kind="percent_off",
                                   percent=D("10"))
        self.assertEqual(self.post(self.manager, "offer_toggle", None, far.id).status_code, 404)
        self.assertEqual(self.post(self.manager, "offer_delete", None, far.id).status_code, 404)

    def test_another_restaurants_offer_is_not_found(self):
        stranger = Tenant.objects.create(name="Other Pub", tenant_type="pub")
        theirs = Offer.objects.create(tenant=stranger, name="Theirs", kind="percent_off", percent=D("10"))
        self.assertEqual(self.post(self.owner, "offer_toggle", None, theirs.id).status_code, 404)

    def test_without_the_feature_there_is_no_screen(self):
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="offers", enabled=False)
        self.assertEqual(self.create().status_code, 403)
        client = Client()
        client.force_login(self.owner)
        self.assertEqual(client.get(reverse("setup_offers")).status_code, 403)
        self.assertNotIn(reverse("setup_offers"), client.get("/setup/").content.decode())


class SwitchingTest(OfferSetupBase):
    def offer(self, outlet="same", name="Bar 10%"):
        return Offer.objects.create(tenant=self.tenant, outlet=self.outlet if outlet == "same" else outlet,
                                    name=name, kind="percent_off", percent=D("10"))

    def test_pause_and_switch_on_again(self):
        offer = self.offer()
        self.assertEqual(self.post(self.manager, "offer_toggle", None, offer.id).json(), {"success": True, "is_active": False})
        offer.refresh_from_db()
        self.assertIsNotNone(offer.paused_at)
        self.post(self.manager, "offer_toggle", None, offer.id)
        offer.refresh_from_db()
        self.assertTrue(offer.is_active)
        self.assertIsNone(offer.paused_at)

    def test_removing_archives_it(self):
        offer = self.offer()
        self.assertEqual(self.post(self.owner, "offer_delete", None, offer.id).status_code, 200)
        offer.refresh_from_db()
        self.assertIsNotNone(offer.archived_at)
        client = Client()
        client.force_login(self.owner)
        self.assertNotIn("Bar 10%", client.get(reverse("setup_offers")).content.decode())

    def test_pause_all_for_a_bad_night(self):
        mine, everywhere = self.offer(), self.offer(outlet=None, name="Everywhere")
        far = self.offer(outlet=self.other_outlet, name="Far")
        self.assertEqual(self.post(self.manager, "offers_pause_all").json(), {"success": True, "paused": 1})
        for offer, active in ((mine, False), (everywhere, True), (far, True)):
            offer.refresh_from_db()
            self.assertEqual(offer.is_active, active, offer.name)
        self.assertEqual(self.post(self.owner, "offers_pause_all").json()["paused"], 1)   # the all-outlet one
        mine.refresh_from_db()
        self.assertIsNotNone(mine.paused_at)


class ThePageTest(OfferSetupBase):
    def page(self):
        client = Client()
        client.force_login(self.owner)
        resp = client.get(reverse("setup_offers"))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_the_setup_hub_links_to_it(self):
        client = Client()
        client.force_login(self.owner)
        self.assertIn(reverse("setup_offers"), client.get("/setup/").content.decode())

    def test_the_list_says_what_each_offer_does(self):
        self.create(template="happy_hour", name="Happy hour 25%", percent="25", category_ids=[self.bar.id],
                    days=[4], start_time="20:00", end_time="02:00")
        page = self.page()
        self.assertIn("Happy hour 25%", page)
        self.assertIn("25% off", page)
        self.assertIn("Fri 20:00-02:00", page)
        self.assertIn("(category)", page)

    def example(self, *taps, read=()):
        """Run the page's own script in Node and return the example bill's HTML."""
        _needs_node(self)
        page = self.page()
        values = dict(re.findall(r'id="(f-[\w-]+)"[^>]*\svalue="([^"]*)"', page))
        runner = HARNESS.replace(
            "      if (id in page.json) el.textContent = page.json[id];",
            "      if (id in page.json) el.textContent = page.json[id];\n"
            "      if (page.values && id in page.values) el.value = page.values[id];",
        )
        runner = runner[:runner.index("const el = id =>")] + (
            'const fields = {}; (page.read || []).forEach(id => { const el = document.getElementById(id); '
            'fields[id] = el ? (id === "form-title" ? el.textContent : el.value) : null; });'
            'const dump = e => [e.textContent || "", ...(e.children || []).map(dump)].filter(Boolean).join(" | ");process.stdout.write(JSON.stringify({errors, fields, example: dump(document.getElementById("example"))}));')
        self.assertIn("page.values", runner)
        done = subprocess.run(
            [NODE, "-e", runner], capture_output=True, text=True, timeout=60, check=True,
            input=json.dumps({
                "files": [str(STATIC_JS / n) for n in re.findall(r'<script src="/static/js/([\w.]+)"', page)
                          if (STATIC_JS / n).exists()],
                "ids": re.findall(r'\sid="([^"]+)"', page),
                "json": dict(re.findall(r'<script id="([^"]+)" type="application/json">(.*?)</script>', page, re.S)),
                # Comments out first: the base template's head has a comment
                # that mentions "<script>" in its prose.
                "scripts": re.findall(r"<script>(.*?)</script>", re.sub(r"<!--.*?-->", "", page, flags=re.S), re.S),
                "values": values, "taps": list(taps), "read": list(read),
            }),
        )
        out = json.loads(done.stdout)
        self.assertEqual(out["errors"], [])
        self.fields = out["fields"]
        return html.unescape(out["example"])

    def test_the_example_bill_prices_buy_two_get_one(self):
        # Whole menu, buy 2 get 1: two Craft (2,500) and one Lager (2,300); the
        # Lager is free. Beer VAT is 0% here.
        example = self.plain(self.example())
        self.assertIn("Buy 2 pitchers, get 1 free 2300.00", example)         # under the free Lager
        self.assertIn("Offers | 2300.00", example)
        self.assertIn("Guest pays | 5000", example)
        self.assertIn("The guest saves 2300.00.", example)

    def test_the_example_follows_the_kind_of_offer(self):
        example = self.plain(self.example(("PERCENT", "setTemplate('percent_off')")))
        # 25% off one of each: 4,800 less 1,200.
        self.assertIn("Offers | 1200.00", example)
        self.assertIn("Guest pays | 3600", example)

    @staticmethod
    def plain(example):
        """The example without its currency and minus signs, one space apart."""
        return re.sub(r"\s+", " ", re.sub(r"[^\x00-\x7f]", "", example))


class EditTest(OfferSetupBase):
    """Fixing an offer made by mistake: open bills follow at once, paid bills
    never move, and the change is on record."""
    page = ThePageTest.page
    example = ThePageTest.example

    def half_off_lager(self):
        resp = self.create(template="percent_off", name="Lager deal", percent="50", dish_ids=[self.lager.id])
        self.assertEqual(resp.status_code, 200, resp.content)
        return Offer.objects.get(pk=resp.json()["id"])

    def bill(self, status="open"):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, source="dine_in", status="open")
        add_items_to_order(self.owner, order, [{"id": self.lager.id, "quantity": 1}])
        if status != "open":
            Order.objects.filter(pk=order.pk).update(status=status)
        order.refresh_from_db()
        return order

    def edit(self, offer, user=None, **fields):
        body = {"template": "percent_off", "name": "Lager deal", "percent": "5", "dish_ids": [self.lager.id]}
        body.update(fields)
        return self.post(user or self.manager, "offer_update", body, offer.id)

    def test_fixing_50_to_5_corrects_open_bills_now_and_never_paid_ones(self):
        offer = self.half_off_lager()
        paid, open_bill = self.bill(status="paid"), self.bill()
        self.assertEqual((paid.offer_total, open_bill.offer_total), (D("1150.00"), D("1150.00")))

        resp = self.edit(offer)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["bills"], 1)
        self.assertEqual(resp.json()["changes"], [{"field": "Kind", "before": "50% off", "after": "5% off"}])

        open_bill.refresh_from_db()
        self.assertEqual((open_bill.offer_total, open_bill.grand_total), (D("115.00"), D("2185")))
        paid.refresh_from_db()
        line = paid.items.get()
        self.assertEqual((paid.offer_total, line.offer_discount, line.offer_name), (D("1150.00"), D("1150.00"), "Lager deal"))

    def test_the_change_is_recorded_and_shown_on_the_row(self):
        offer = self.half_off_lager()
        self.edit(offer, name="Lager 5%")
        change = offer.changes.get()
        self.assertEqual(change.changed_by, self.manager)
        self.assertEqual(change.changes, [{"field": "Name", "before": "Lager deal", "after": "Lager 5%"},
                                          {"field": "Kind", "before": "50% off", "after": "5% off"}])
        client = Client()
        client.force_login(self.owner)
        page = client.get(reverse("setup_offers")).content.decode()
        self.assertIn("by ot_manager", page)
        self.assertIn("Kind: 50% off &rarr; 5% off", page)

    def test_an_edit_replaces_what_it_covers_and_when(self):
        offer = self.half_off_lager()
        resp = self.edit(offer, template="happy_hour", category_ids=[self.bar.id], dish_ids=[],
                         days=[5, 6], start_time="12:00", end_time="16:00")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([t.category_id for t in offer.targets.all()], [self.bar.id])
        self.assertEqual(str(offer.windows.get()), "Sat, Sun 12:00-16:00")
        fields = {c["field"] for c in resp.json()["changes"]}
        self.assertEqual(fields, {"Kind", "Covers", "When"})

    def test_an_edit_is_checked_like_a_new_offer_and_a_refused_one_changes_nothing(self):
        offer = self.half_off_lager()
        resp = self.edit(offer, percent="0")
        self.assertEqual(resp.json()["error"], "The percent off must be more than 0.")
        offer.refresh_from_db()
        self.assertEqual(offer.percent, D("50.00"))
        self.assertFalse(offer.changes.exists())

    def test_saving_without_a_difference_records_nothing(self):
        offer = self.half_off_lager()
        self.bill()
        resp = self.edit(offer, percent="50")
        self.assertEqual((resp.json()["changes"], resp.json()["bills"]), ([], 0))
        self.assertFalse(offer.changes.exists())

    def test_who_may_edit(self):
        offer = self.half_off_lager()
        self.assertEqual(self.edit(offer, user=self.cashier).status_code, 403)
        far_manager = self.user("ot_far", "manager", outlet=self.other_outlet)
        self.assertEqual(self.edit(offer, user=far_manager).status_code, 404)
        offer.archive()
        self.assertEqual(self.edit(offer).status_code, 404)

    def test_moving_an_offer_to_another_outlet_takes_it_off_this_ones_open_bills(self):
        offer = self.half_off_lager()
        open_bill = self.bill()
        resp = self.post(self.owner, "offer_update", {"template": "percent_off", "name": "Lager deal",
                                                      "percent": "50", "outlet_id": self.other_outlet.id}, offer.id)
        self.assertEqual(resp.status_code, 200, resp.content)
        open_bill.refresh_from_db()
        self.assertEqual(open_bill.offer_total, D("0.00"))

    def test_edit_loads_the_offer_into_the_form(self):
        offer = self.half_off_lager()
        offer.refresh_from_db()
        self.assertIn('"template": "percent_off"', self.page())
        self.example(("EDIT", f"startEdit({offer.id})"), read=("f-name", "f-pct", "form-title"))
        self.assertEqual(self.fields, {"f-name": "Lager deal", "f-pct": "50.00", "form-title": "Edit offer"})

