"""
The GST Rates page (P1): it offers only the rates a restaurant bill can carry
since GST 2.0 (0%, 5%, 18%), explains them honestly, refuses the retired 12%
and 28%, and flags any dish still on one of them.

Run: python manage.py test menu.tests.test_gst_rates
"""
import json
from decimal import Decimal as D

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from tenants.models import Outlet, Tenant


class GstRatesPageTest(TestCase):

    def setUp(self):
        tenant = Tenant.objects.create(name="GST Rates Test")
        outlet = Outlet.objects.create(tenant=tenant, name="Main")
        self.owner = User.objects.create_user(username="gst_rates_owner", password="x",
                                              tenant=tenant, outlet=outlet, role="owner")
        self.category = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Food")
        self.dosa = MenuItem.objects.create(tenant=tenant, outlet=outlet, category=self.category,
                                            name="Dosa", price=D("90"), gst_percentage=D("5"))
        self.water = MenuItem.objects.create(tenant=tenant, outlet=outlet, category=self.category,
                                             name="Water", price=D("20"), gst_percentage=D("12"))
        self.client = Client()
        self.client.force_login(self.owner)

    def set_rate(self, item, rate):
        return self.client.post(reverse("update_item_gst", args=[item.id]),
                                json.dumps({"gst_percentage": rate}), content_type="application/json")

    def test_only_current_gst_rates_are_offered(self):
        response = self.client.get(reverse("gst_management"))
        rates = response.context["gst_rates"]
        self.assertEqual([r["value"] for r in rates], ["0.00", "5.00", "18.00"])
        for rate in rates:
            self.assertNotIn("\u2014", rate["label"])          # no em dash
            self.assertNotIn("Liquor", rate["label"])
        self.assertNotContains(response, "AC / Liquor")
        self.assertContains(response, "Alcohol is not under GST at all")

    def test_a_dish_on_a_retired_rate_is_flagged_not_shown_as_0_percent(self):
        response = self.client.get(reverse("gst_management"))
        self.assertEqual(response.context["retired_count"], 1)
        self.assertContains(response, "12%, no longer a GST rate")
        self.assertContains(response, f'id="retired-{self.water.id}"')
        self.assertNotContains(response, f'id="retired-{self.dosa.id}"')

    def test_retired_rates_are_refused(self):
        for rate in ("12.00", "28.00", "40.00"):
            self.assertEqual(self.set_rate(self.dosa, rate).status_code, 400, rate)
        self.dosa.refresh_from_db()
        self.assertEqual(self.dosa.gst_percentage, D("5.00"))

    def test_a_current_rate_is_saved(self):
        self.assertEqual(self.set_rate(self.water, "18.00").status_code, 200)
        self.water.refresh_from_db()
        self.assertEqual(self.water.gst_percentage, D("18.00"))
        response = self.client.get(reverse("gst_management"))
        self.assertEqual(response.context["retired_count"], 0)
        self.assertNotContains(response, 'id="retired-banner"')

    def test_a_whole_category_can_move_but_not_to_a_retired_rate(self):
        url = reverse("update_category_gst", args=[self.category.id])
        bad = self.client.post(url, json.dumps({"gst_percentage": "28.00"}), content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        good = self.client.post(url, json.dumps({"gst_percentage": "5.00"}), content_type="application/json")
        self.assertEqual(good.status_code, 200)
        self.water.refresh_from_db()
        self.assertEqual(self.water.gst_percentage, D("5.00"))
