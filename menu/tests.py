# menu/tests.py
from django.test import TestCase, Client
from django.urls import reverse
from decimal import Decimal
import json

from tenants.models import Tenant, Outlet
from accounts.models import User
from menu.models import (
    MenuCategory,
    MenuItem,
    ModifierGroup,
    Modifier,
    MenuItemModifierGroup
)


class MenuModelTests(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Test Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main Outlet"
        )

        self.category = MenuCategory.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Burgers"
        )

    def test_create_menu_item(self):

        item = MenuItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            category=self.category,
            name="Classic Burger",
            price=Decimal("199.00")
        )

        self.assertEqual(item.name, "Classic Burger")
        self.assertEqual(item.price, Decimal("199.00"))

    def test_negative_price_not_allowed(self):

        item = MenuItem(
            tenant=self.tenant,
            outlet=self.outlet,
            category=self.category,
            name="Bad Burger",
            price=Decimal("-10")
        )

        with self.assertRaises(Exception):
            item.full_clean()


class ModifierTests(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Outlet"
        )

        self.category = MenuCategory.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Pizza"
        )

        self.item = MenuItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            category=self.category,
            name="Margherita",
            price=250
        )

        self.group = ModifierGroup.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Extra Toppings"
        )

    def test_modifier_creation(self):

        modifier = Modifier.objects.create(
            group=self.group,
            name="Extra Cheese",
            price=50
        )

        self.assertEqual(modifier.name, "Extra Cheese")

    def test_modifier_group_link(self):

        link = MenuItemModifierGroup.objects.create(
            menu_item=self.item,
            modifier_group=self.group
        )

        self.assertEqual(link.menu_item, self.item)


class MenuViewTests(TestCase):

    def setUp(self):

        self.client = Client()

        self.tenant = Tenant.objects.create(name="Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Outlet"
        )

        self.user = User.objects.create_user(
            username="owner",
            password="testpass",
            role="owner",
            tenant=self.tenant,
            outlet=self.outlet
        )

        self.client.login(username="owner", password="testpass")

        self.category = MenuCategory.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Drinks"
        )

    def test_create_category(self):

        response = self.client.post(
            reverse("create_category"),
            data=json.dumps({"name": "Desserts"}),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        self.assertTrue(
            MenuCategory.objects.filter(name="Desserts").exists()
        )

    def test_create_menu_item(self):

        response = self.client.post(
            reverse("create_menu_item"),
            data=json.dumps({
                "name": "Coke",
                "price": 40,
                "category": self.category.id
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        self.assertTrue(
            MenuItem.objects.filter(name="Coke").exists()
        )


class MenuSecurityTests(TestCase):

    def setUp(self):

        self.client = Client()

        self.tenant = Tenant.objects.create(name="Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Outlet"
        )

        self.waiter = User.objects.create_user(
            username="waiter",
            password="testpass",
            role="waiter",
            tenant=self.tenant,
            outlet=self.outlet
        )

    def test_waiter_cannot_access_menu_management(self):

        self.client.login(username="waiter", password="testpass")

        response = self.client.get(reverse("menu_management"))

        self.assertEqual(response.status_code, 403)