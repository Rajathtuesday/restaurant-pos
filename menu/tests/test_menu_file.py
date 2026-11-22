"""
Menu › Menu file: a menu saved as a file and loaded into another outlet or a
box (22 Nov 2026). A box has no AI menu import, so Rasova reads the menu
photo in the cloud and the box loads the file.

Run: python manage.py test menu.tests.test_menu_file
"""
import json
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup, VatClass
from menu.services import menu_file as mf
from setup.models import KitchenStation
from tenants.models import Outlet, Tenant


class MenuFileWorld(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Copper Tap", slug="mf-copper", tenant_type="pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Indiranagar")
        self.empty = Outlet.objects.create(tenant=self.tenant, name="Koramangala")
        self.owner = User.objects.create_user(username="mf-owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        self.kitchen = KitchenStation.objects.create(tenant=self.tenant, outlet=self.outlet, name="Kitchen", is_default=True)
        self.bar = KitchenStation.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar", made_on_send=True)
        self.vat = VatClass.objects.create(tenant=self.tenant, outlet=self.outlet, name="IMFL", rate=D("35.00"))
        self.starters = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Starters", display_order=1)
        self.drinks = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Drinks", display_order=2)
        self.paneer = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=self.starters,
                                              name="Paneer Tikka", price=D("280"), gst_percentage=D("5.00"),
                                              station=self.kitchen, description="Char-grilled", is_veg=True)
        self.whisky = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=self.drinks,
                                              name="Single Malt 30ml", price=D("450"), gst_percentage=D("0.00"),
                                              vat_class=self.vat, station=self.bar)
        self.spice = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Spice", max_select=1)
        Modifier.objects.create(group=self.spice, name="Extra Hot", price=D("0"))
        Modifier.objects.create(group=self.spice, name="Cheese", price=D("40"))
        MenuItemModifierGroup.objects.create(menu_item=self.paneer, modifier_group=self.spice)

    def as_owner(self, outlet=None):
        if outlet is not None:
            User.objects.filter(pk=self.owner.pk).update(outlet=outlet)
            self.owner.refresh_from_db()
        c = Client()
        c.force_login(self.owner)
        return c

    def upload(self, client, data, name="menu.json"):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        return client.post("/menu/file/check/", {"menu_file": SimpleUploadedFile(name, body, "application/json")})

    def token(self, page):
        import re
        return re.search(r'name="token" value="([^"]+)"', page.content.decode()).group(1)


class SaveTest(MenuFileWorld):
    def test_the_file_carries_the_whole_menu(self):
        r = self.as_owner().get("/menu/file/download/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('attachment; filename="rasova-menu-copper-tap-', r["Content-Disposition"])
        data = json.loads(r.content)
        self.assertEqual((data["format"], data["version"]), ("rasova-menu", 1))
        self.assertEqual([s["name"] for s in data["stations"]], ["Kitchen", "Bar"])
        self.assertEqual(data["vat_classes"], [{"name": "IMFL", "rate": "35.00"}])
        starters = next(c for c in data["categories"] if c["name"] == "Starters")
        paneer = starters["items"][0]
        self.assertEqual((paneer["name"], paneer["price"], paneer["station"], paneer["modifier_groups"]),
                         ("Paneer Tikka", "280.00", "Kitchen", ["Spice"]))
        whisky = next(c for c in data["categories"] if c["name"] == "Drinks")["items"][0]
        self.assertEqual((whisky["vat_class"], whisky["gst_percentage"]), ("IMFL", "0.00"))
        self.assertEqual({m["name"] for m in data["modifier_groups"][0]["modifiers"]}, {"Extra Hot", "Cheese"})

    def test_only_this_outlets_menu_is_in_it(self):
        other = Tenant.objects.create(name="Elsewhere", slug="mf-else")
        o = Outlet.objects.create(tenant=other, name="Theirs")
        MenuCategory.objects.create(tenant=other, outlet=o, name="SECRETCATEGORY")
        MenuCategory.objects.create(tenant=self.tenant, outlet=self.empty, name="OTHEROUTLET")
        body = self.as_owner().get("/menu/file/download/").content.decode()
        self.assertNotIn("SECRETCATEGORY", body)
        self.assertNotIn("OTHEROUTLET", body)

    def test_owner_and_manager_only(self):
        cashier = User.objects.create_user(username="mf-cash", password="pw", role="cashier",
                                           tenant=self.tenant, outlet=self.outlet)
        c = Client()
        c.force_login(cashier)
        for url in ("/menu/file/", "/menu/file/download/"):
            self.assertNotEqual(c.get(url).status_code, 200, url)


class LoadTest(MenuFileWorld):
    def saved(self):
        return mf.export_menu(self.outlet)

    def test_a_round_trip_rebuilds_the_menu_in_an_empty_outlet(self):
        data = self.saved()
        c = self.as_owner(self.empty)
        page = self.upload(c, data)
        self.assertContains(page, 'id="newItems">2<')
        self.assertFalse(MenuItem.objects.filter(outlet=self.empty).exists(), "checking changes nothing")
        r = c.post("/menu/file/load/", {"token": self.token(page)})
        self.assertRedirects(r, "/menu/", fetch_redirect_response=False)
        paneer = MenuItem.objects.get(outlet=self.empty, name="Paneer Tikka")
        self.assertEqual((paneer.price, paneer.station.name, paneer.category.name), (D("280.00"), "Kitchen", "Starters"))
        self.assertEqual(list(paneer.modifier_groups.values_list("modifier_group__name", flat=True)), ["Spice"])
        whisky = MenuItem.objects.get(outlet=self.empty, name="Single Malt 30ml")
        self.assertEqual((whisky.vat_class.name, whisky.vat_class.outlet_id), ("IMFL", self.empty.id))
        self.assertTrue(KitchenStation.objects.get(outlet=self.empty, name="Kitchen").is_default)
        self.assertTrue(KitchenStation.objects.get(outlet=self.empty, name="Bar").made_on_send)
        self.assertEqual(Modifier.objects.filter(group__outlet=self.empty).count(), 2)

    def test_loading_merges_by_name_and_never_deletes(self):
        data = self.saved()
        data["categories"][0]["items"][0]["price"] = "300.00"        # Paneer Tikka, new price
        data["categories"][0]["items"].append({"name": "Chilli Potato", "price": "180"})
        MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=self.starters, name="Old Special", price=D("99"))
        c = self.as_owner()
        page = self.upload(c, data)
        self.assertContains(page, 'id="newItems">1<')
        self.assertContains(page, 'id="updatedItems">2<')
        c.post("/menu/file/load/", {"token": self.token(page)})
        self.paneer.refresh_from_db()
        self.assertEqual(self.paneer.price, D("300.00"))
        self.assertTrue(MenuItem.objects.filter(outlet=self.outlet, name="Chilli Potato").exists())
        self.assertTrue(MenuItem.objects.filter(outlet=self.outlet, name="Old Special").exists(), "nothing is removed")
        self.assertEqual(MenuCategory.objects.filter(outlet=self.outlet).count(), 2)
        self.assertEqual(MenuItemModifierGroup.objects.filter(menu_item=self.paneer).count(), 1, "no second link")

    def test_a_check_loads_once_and_only_for_who_checked_it(self):
        c = self.as_owner(self.empty)
        token = self.token(self.upload(c, self.saved()))
        manager = User.objects.create_user(username="mf-mgr", password="pw", role="manager",
                                           tenant=self.tenant, outlet=self.empty)
        m = Client()
        m.force_login(manager)
        m.post("/menu/file/load/", {"token": token})
        self.assertFalse(MenuItem.objects.filter(outlet=self.empty).exists(), "another person can't load my check")
        c.post("/menu/file/load/", {"token": token})
        c.post("/menu/file/load/", {"token": token})               # a reload
        self.assertEqual(MenuItem.objects.filter(outlet=self.empty, name="Paneer Tikka").count(), 1)

    def test_the_live_demo_cannot_load_a_menu(self):
        from orders.services.demo_seed import DEMO_OWNER_USERNAME
        User.objects.filter(pk=self.owner.pk).update(username=DEMO_OWNER_USERNAME, outlet=self.empty)
        self.owner.refresh_from_db()
        c = Client()
        c.force_login(self.owner)
        session = c.session
        session["demo_trailer_mode"] = True
        session.save()
        self.upload(c, self.saved())
        self.assertFalse(MenuItem.objects.filter(outlet=self.empty).exists())


class BadFileTest(MenuFileWorld):
    def refused(self, data, words):
        c = self.as_owner(self.empty)
        page = self.upload(c, data)
        self.assertRedirects(page, "/menu/file/", fetch_redirect_response=False)
        follow = c.get("/menu/file/")
        self.assertContains(follow, words)
        self.assertFalse(MenuItem.objects.filter(outlet=self.empty).exists())

    def with_item(self, **item):
        data = mf.export_menu(self.outlet)
        data["categories"][0]["items"][0].update(item)
        return data

    def test_not_a_menu_file(self):
        self.refused(b"\x89PNG not json", "isn&#x27;t a Rasova menu file")
        self.refused({"format": "something-else"}, "isn&#x27;t a Rasova menu file")
        self.refused({"format": "rasova-menu", "version": 2}, "newer Rasova")

    def test_amounts_and_taxes_that_cant_be_billed(self):
        self.refused(self.with_item(price="-5"), "Paneer Tikka&#x27;s price must be an amount")
        self.refused(self.with_item(price="NaN"), "Paneer Tikka&#x27;s price")
        self.refused(self.with_item(gst_percentage="12"), "Rasova bills at 0%, 5% or 18%")
        self.refused(self.with_item(vat_class="IMFL"), "its GST must be 0%")
        self.refused(self.with_item(vat_class="Beer VAT", gst_percentage="0"), "isn&#x27;t in the file")
        self.refused(self.with_item(parcel_charge="10000"), "parcel charge must be an amount from 0 to 9,999.99")

    def test_damaged_shapes_are_refused(self):
        self.refused(self.with_item(name=""), "name in Starters is empty")
        self.refused(self.with_item(is_veg="yes"), "must be true or false")
        self.refused(self.with_item(modifier_groups=["Sauces"]), "modifier group Sauces, which isn")
        data = mf.export_menu(self.outlet)
        data["categories"].append(dict(data["categories"][0]))
        self.refused(data, "is in the file twice")

    def test_a_composition_outlet_cannot_take_liquor(self):
        Outlet.objects.filter(pk=self.empty.pk).update(is_composition_scheme=True)
        self.refused(mf.export_menu(self.outlet), "composition")

    def test_a_huge_file_is_refused(self):
        self.refused(b" " * (mf.MAX_BYTES + 10), "too big")

    def test_a_station_named_by_a_dish_but_missing_from_the_list_is_made(self):
        data = self.with_item(station="Tandoor")
        clean = mf.read_menu_file(json.dumps(data).encode())
        mf.apply_import(self.empty, clean)
        self.assertEqual(MenuItem.objects.get(outlet=self.empty, name="Paneer Tikka").station.name, "Tandoor")


class MenuPageLinksTest(MenuFileWorld):
    def test_the_menu_page_links_to_the_menu_file(self):
        self.assertContains(self.as_owner().get("/menu/"), 'id="menuFileLink"')

    @override_settings(BOX=True, ALLOWED_HOSTS=["*"])
    def test_a_box_has_no_ai_import_button_and_points_at_the_menu_file(self):
        # The button opened a page a box doesn't serve.
        from box.models import BoxState
        BoxState.objects.update_or_create(pk=1, defaults={"setup_done_at": timezone.now()})
        c = self.as_owner()
        menu = c.get("/menu/").content.decode()
        self.assertNotIn('data-action="menu.aiImport"', menu)
        self.assertIn('id="menuFileLink"', menu)
        self.assertContains(c.get("/menu/file/"), "A box has no AI menu import")
