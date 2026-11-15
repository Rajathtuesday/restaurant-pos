"""
One name, one dish, per outlet (menu/dish_names.py, 15 Nov 2026).

Why: the Add Item form accepted a second dish called "uta" (Rs 200 and
Rs 170 in the same category), and the KOT, the bill and the QR menu show a
dish by its name alone. The imports checked only an exact name in the same
category, so "Uta " or "UTA", or the same dish under another category,
slipped in too.
Run: python manage.py test menu.tests.test_dish_names
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from core.tests.test_tenant_walk import Restaurant
from menu.dish_names import add_unless_named, clean, same_name
from menu.models import MenuCategory, MenuItem


class DishNamesTest(TestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "names-alpha")
        self.uta = MenuItem.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, category=self.r.category,
                                           name="Uta", price=Decimal("200"))
        self.drinks = MenuCategory.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name="Drinks")
        self.client = Client()
        self.client.force_login(self.r.owner)

    def add(self, name, category=None):
        return self.client.post(reverse("create_menu_item"), data={
            "name": name, "price": "170", "category": (category or self.r.category).id, "is_veg": "true"})

    def count(self, name="uta"):
        return MenuItem.objects.filter(outlet=self.r.outlet, name__iexact=name).count()

    def test_a_second_dish_with_the_same_name_is_refused_and_says_which(self):
        for name in ("Uta", "uta", "  UTA "):
            resp = self.add(name)
            self.assertEqual(resp.status_code, 409, (name, resp.content))
            self.assertIn('"Uta"', resp.json()["error"])
        self.assertEqual(self.count(), 1)

    def test_the_same_name_under_another_category_is_refused_too(self):
        self.assertEqual(self.add("uta", category=self.drinks).status_code, 409)
        self.assertEqual(self.count(), 1)

    def test_a_new_name_is_added_trimmed(self):
        self.assertEqual(self.add("  Masala   Chai ").status_code, 200)
        self.assertTrue(MenuItem.objects.filter(outlet=self.r.outlet, name="Masala Chai").exists())

    def test_renaming_to_a_taken_name_is_refused_but_a_dish_keeps_its_own(self):
        chai = MenuItem.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, category=self.r.category,
                                       name="Chai", price=Decimal("40"))
        edit = lambda dish, name: self.client.post(reverse("update_menu_item", args=[dish.id]), data={  # noqa: E731
            "name": name, "price": "40", "category": self.r.category.id, "is_veg": "true"})
        self.assertEqual(edit(chai, "UTA").status_code, 409)
        self.assertEqual(edit(self.uta, "uta").status_code, 200)        # its own name, another case
        chai.refresh_from_db()
        self.assertEqual(chai.name, "Chai")

    def test_another_outlet_may_use_the_name(self):
        other = Restaurant("Beta", "names-beta")
        self.assertIsNone(same_name(other.tenant, other.outlet, "Uta"))

    def test_the_menu_import_leaves_a_dish_already_on_the_menu(self):
        from menu.tasks import ai_import_menu
        menu = [{"category": "Drinks", "items": [{"name": "UTA", "price": 999, "is_veg": True},
                                                {"name": "Lassi", "price": 80, "is_veg": True}]}]
        with patch("core.ai_service.AIService.parse_menu", return_value=menu):
            ai_import_menu.apply(args=[self.r.tenant.id, self.r.outlet.id, "x", None, None])
        self.assertEqual(self.count(), 1)
        self.uta.refresh_from_db()
        self.assertEqual(self.uta.price, Decimal("200.00"))
        self.assertTrue(MenuItem.objects.filter(outlet=self.r.outlet, name="Lassi").exists())

    def test_the_helpers(self):
        self.assertEqual(clean("  Masala   Chai "), "Masala Chai")
        dish, created = add_unless_named(self.r.tenant, self.r.outlet, self.drinks, " uTa ", {"price": Decimal("1")})
        self.assertEqual((dish.id, created), (self.uta.id, False))
