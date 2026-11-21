"""
The running order page's server side: what the page is told it may do,
and what each poll sends.

Why: the page kept its own list of roles for each button. It showed Comp
to owners and managers only, while the server lets a captain make a dish
free (orders.tests.test_captain_permissions), and it showed Cancel Order
to a waiter, whom the server refuses. Since 14 Nov 2026 the page asks the
views themselves (core.decorators.may), so a button shows exactly when
the server would take the tap. And a dish already made free still offered
Comp, because the poll never said it was free.

Run: python manage.py test orders.tests.test_running_order_page
"""
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from core.decorators import feature_required, may, role_required
from tenants.models import TenantFeatureOverride
from core.tests.test_tenant_walk import Restaurant
from orders.views.order_views import running_order_page_data

User = get_user_model()


class MayTests(TestCase):
    """may(user, view) answers what the view's own decorators would."""

    def setUp(self):
        self.r = Restaurant("Alpha", "may-alpha")

    def user(self, role, **extra):
        return User.objects.create_user(username=f"may-{role}", password="pw", role=role,
                                        tenant=self.r.tenant, outlet=self.r.outlet, **extra)

    def test_roles_and_features_both_count(self):
        @role_required("owner", "captain")
        @feature_required("kitchen_display")
        def view(request):
            return None

        self.assertTrue(may(self.r.owner, view))
        self.assertTrue(may(self.user("captain"), view))
        self.assertFalse(may(self.r.staff, view))  # a waiter
        TenantFeatureOverride.objects.filter(tenant=self.r.tenant, feature="kitchen_display").update(enabled=False)
        # Overrides are cached on the tenant object for a request: a fresh one.
        self.assertFalse(may(User.objects.get(pk=self.r.owner.pk), view))

    def test_a_superuser_may_do_anything(self):
        @role_required("owner")
        def view(request):
            return None

        self.assertTrue(may(self.user("waiter", is_superuser=True), view))

    def test_two_role_lists_on_one_view_both_apply(self):
        @role_required("owner", "manager")
        @role_required("manager", "captain")
        def view(request):
            return None

        self.assertEqual(view.allowed_roles, frozenset({"manager"}))

    def test_a_view_with_no_role_list_is_open_to_any_role(self):
        @feature_required("running_order")
        def view(request):
            return None

        self.assertTrue(may(self.r.staff, view))


class PageDataTests(TestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "ro-alpha")

    def can(self, user):
        return running_order_page_data(self.r.order, user)["can"]

    def test_the_owner_may_do_everything(self):
        self.assertEqual(self.can(self.r.owner), {
            "edit": True, "cancel_order": True, "serve": True, "comp": True, "edit_served": True,
            "approve": True,
        })

    def test_a_captain_may_make_a_dish_free_but_not_change_a_served_one(self):
        captain = User.objects.create_user(username="ro-captain", password="pw", role="captain",
                                           tenant=self.r.tenant, outlet=self.r.outlet)
        can = self.can(captain)
        self.assertTrue(can["comp"])
        self.assertTrue(can["edit"])
        self.assertFalse(can["edit_served"])

    def test_a_waiter_may_serve_and_nothing_else(self):
        self.assertEqual(self.can(self.r.staff), {
            "edit": False, "cancel_order": False, "serve": True, "comp": False, "edit_served": False,
            # A guest's QR dish: anyone who may approve it (approve_items has
            # no role list) gets the tick and the cross.
            "approve": True,
        })

    def test_serve_follows_the_kitchen_display_feature(self):
        TenantFeatureOverride.objects.filter(tenant=self.r.tenant, feature="kitchen_display").update(enabled=False)
        self.assertFalse(self.can(User.objects.get(pk=self.r.owner.pk))["serve"])

    def test_the_addresses_are_this_order_and_the_endpoints(self):
        urls = running_order_page_data(self.r.order, self.r.owner)["urls"]
        self.assertEqual(urls["data"], reverse("running-order-data", args=[self.r.order.id]))
        self.assertEqual(urls["comp"], reverse("make-complimentary", args=[0]))
        self.assertEqual(urls["bill"], reverse("bill-view", args=[self.r.order.id]))

    def test_the_page_renders_its_data_once_and_no_cancel_button_for_a_waiter(self):
        client = Client()
        client.force_login(self.r.staff)
        html = client.get(reverse("running-order", args=[self.r.order.id])).content.decode()
        self.assertEqual(html.count('id="page-data"'), 1)
        self.assertNotIn('data-action="order.cancel"', html)
        client.force_login(self.r.owner)
        html = client.get(reverse("running-order", args=[self.r.order.id])).content.decode()
        self.assertIn('data-action="order.cancel"', html)


class PollTests(TestCase):

    def test_a_free_dish_says_so(self):
        r = Restaurant("Alpha", "poll-alpha")
        client = Client()
        client.force_login(r.owner)
        url = reverse("running-order-data", args=[r.order.id])
        self.assertIs(client.get(url).json()["items"][0]["is_complimentary"], False)
        r.order_item.is_complimentary = True
        r.order_item.save(update_fields=["is_complimentary"])
        self.assertIs(client.get(url).json()["items"][0]["is_complimentary"], True)


class NoBrowserPopupTests(SimpleTestCase):

    def test_the_page_and_its_modules_never_use_a_browser_popup(self):
        """prompt(), alert() and confirm() never show in the Android app."""
        from pathlib import Path

        from django.conf import settings
        root = Path(settings.BASE_DIR)
        files = [root / "orders/templates/orders/running_order.html", *sorted((root / "static/js/running_order").glob("*.js"))]
        for path in files:
            text = path.read_text(encoding="utf-8")
            for popup in ("prompt(", "alert(", "confirm("):
                self.assertNotIn(popup, text, f"{path.name} calls {popup}")
