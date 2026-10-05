"""
Only messages written for the user reach the screen (core/errors.py).

GitHub's code scanning (CodeQL, py/stack-trace-exposure) flagged 43 views
that answered with str(e). Most were Rasova's own messages; two were real
leaks: receiving a purchase order answered a TypeError or AttributeError
with Python's own words, and kitchen, order-API and order-source views
caught any ValueError, so an unexpected one would have been shown as well.

Run: python manage.py test core.tests.test_user_errors
"""
import json
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import User
from core.errors import GENERIC_MESSAGE, UserError, user_message
from tenants.models import Outlet, Tenant, TenantFeatureOverride

PYTHON_WORDS = "'NoneType' object has no attribute 'unit'"


class UserMessageTest(SimpleTestCase):
    def test_a_user_error_is_shown_as_written(self):
        self.assertEqual(user_message(UserError("The tips can't be negative.")), "The tips can't be negative.")

    def test_a_validation_error_shows_its_messages(self):
        self.assertEqual(user_message(ValidationError(["Too much.", "Try less."])), "Too much. Try less.")

    def test_anything_else_is_logged_and_never_shown(self):
        with self.assertLogs("pos.core", level="ERROR") as logs:
            self.assertEqual(user_message(AttributeError(PYTHON_WORDS)), GENERIC_MESSAGE)
            self.assertEqual(user_message(ValueError("invalid literal for int() with base 10: 'x'")), GENERIC_MESSAGE)
        self.assertIn("AttributeError", logs.output[0])

    def test_every_message_rasova_writes_is_a_user_error(self):
        from core.validators import NumberInputError
        from inventory.recipe_service import RecipeCrossTenantError, RecipeUnitMismatchError
        from inventory.unit_conversion import IncompatibleUnitsError
        from kitchen.services.kitchen_service import KitchenStateError
        from orders.exceptions import CartError, InventoryError, IssuedBillError, MenuItemError, ModifierError, OrderError
        from orders.services.cart_limits import QuantityError
        from orders.services.discount_policy import DiscountNeedsManager, DiscountRefused
        from orders.views.billing_views import OrderSourceError
        from setup.views.offer_views import OfferInputError
        from setup.views.promo_views import PromoInputError
        for cls in (NumberInputError, RecipeCrossTenantError, RecipeUnitMismatchError, IncompatibleUnitsError,
                    KitchenStateError, CartError, InventoryError, IssuedBillError, MenuItemError, ModifierError,
                    OrderError, QuantityError, DiscountNeedsManager, DiscountRefused, OrderSourceError,
                    OfferInputError, PromoInputError):
            self.assertTrue(issubclass(cls, UserError), cls.__name__)
        # The ones that were ValueErrors still are, so older callers keep working.
        for cls in (NumberInputError, QuantityError, OrderSourceError, KitchenStateError, IncompatibleUnitsError):
            self.assertTrue(issubclass(cls, ValueError), cls.__name__)
        self.assertEqual(IssuedBillError(1, "paid").message,
                         "This bill is already paid, so it can't be changed. Correct it with a refund.")


class ScreensNeverShowPythonsWordsTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Leak Check Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        for feature in ("purchase_orders", "inventory", "kot_system", "kitchen_display"):
            TenantFeatureOverride.objects.create(tenant=self.tenant, feature=feature, enabled=True)
        self.owner = User.objects.create_user(username="leak_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        self.client = Client(raise_request_exception=False)
        self.client.force_login(self.owner)

    def test_receiving_a_purchase_order(self):
        from inventory.models import PurchaseOrder, Supplier
        supplier = Supplier.objects.create(tenant=self.tenant, outlet=self.outlet, name="Agro")
        po = PurchaseOrder.objects.create(tenant=self.tenant, outlet=self.outlet, supplier=supplier, status="ordered")
        with mock.patch.object(PurchaseOrder, "receive_order", side_effect=AttributeError(PYTHON_WORDS)):
            resp = self.client.post(reverse("po_receive", args=[po.id]), data=json.dumps({"items": {}}),
                                    content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("NoneType", resp.content.decode())
        self.assertEqual(resp.json()["error"], "Invalid receiving quantities. Enter a number for each item received.")

    def test_the_kitchen_screen(self):
        # A kitchen state message is shown; an unexpected ValueError is not.
        from kitchen.services.kitchen_service import KitchenStateError
        with mock.patch("kitchen.services.kitchen_service.set_item_preparing",
                        side_effect=KitchenStateError("Invalid state constraint: Item is not 'sent'.")):
            resp = self.client.post(reverse("item-start", args=[1]))
        self.assertEqual((resp.status_code, resp.json()["error"]), (400, "Invalid state constraint: Item is not 'sent'."))
        with mock.patch("kitchen.services.kitchen_service.set_item_preparing",
                        side_effect=ValueError("invalid literal for int() with base 10: 'x'")):
            resp = self.client.post(reverse("item-start", args=[1]))
        self.assertNotIn("invalid literal", resp.content.decode())

    def test_an_order_source_message_still_reaches_the_screen(self):
        resp = self.client.post(reverse("create-order"), data=json.dumps({"source": "martian", "cart": [{"id": 1}]}),
                                content_type="application/json")
        self.assertEqual((resp.status_code, resp.json()["error"]), (400, "Unknown order source."))


class AgentDownloadTest(TestCase):
    def test_only_the_named_files_and_nothing_from_the_url(self):
        client = Client()
        self.assertEqual(client.get(reverse("agent_download", args=["rasova_agent.py"])).status_code, 200)
        for name in ("settings.py", "..%2Fcore%2Fsettings.py", "rasova_agent.pyc"):
            self.assertEqual(client.get(f"/agent/{name}").status_code, 404, name)
