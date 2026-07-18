# core/tests.py
"""
Tests for core/decorators.py.

Coverage:
  - tenant_required now bypasses for superusers, who normally have
    tenant=None and would otherwise be locked out of any view stacked
    with this decorator, including admin/support tooling.
"""
import json

from django.http import HttpResponse
from django.test import TestCase, RequestFactory, Client
from django.urls import reverse

from accounts.models import User
from core.decorators import tenant_required
from tenants.models import Tenant, Outlet


@tenant_required
def _dummy_view(request):
    return HttpResponse("ok")


class TenantRequiredSuperuserBypassTest(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(name="Decorator Test Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

    def test_superuser_with_no_tenant_is_not_blocked(self):
        superuser = User.objects.create_superuser(
            username="platform_admin", password="pw", email="a@a.com",
        )
        self.assertIsNone(superuser.tenant)

        request = self.factory.get("/some-tenant-scoped-view/")
        request.user = superuser
        response = _dummy_view(request)
        self.assertEqual(response.status_code, 200)

    def test_regular_user_without_tenant_still_blocked(self):
        # Existing behavior must be unchanged for non-superusers — this
        # decorator's whole job for everyone else is exactly this check.
        from django.core.exceptions import PermissionDenied

        plain_user = User.objects.create_user(
            username="no_tenant_user", password="pw",
        )
        self.assertFalse(plain_user.is_superuser)
        self.assertIsNone(plain_user.tenant)

        request = self.factory.get("/some-tenant-scoped-view/")
        request.user = plain_user
        with self.assertRaises(PermissionDenied):
            _dummy_view(request)

    def test_regular_user_with_tenant_and_outlet_passes_normally(self):
        user = User.objects.create_user(
            username="scoped_user", password="pw",
            tenant=self.tenant, outlet=self.outlet,
        )
        request = self.factory.get("/some-tenant-scoped-view/")
        request.user = user
        response = _dummy_view(request)
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_user_redirected_to_login(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.get("/some-tenant-scoped-view/")
        request.user = AnonymousUser()
        response = _dummy_view(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class CsrfFailureViewTest(TestCase):
    """
    Django's test Client normally bypasses CSRF checks entirely —
    enforce_csrf_checks=True is required to actually trigger a real
    failure and exercise CSRF_FAILURE_VIEW, same as a real browser would.
    """

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def test_json_request_gets_json_error_not_html(self):
        # No csrfmiddlewaretoken, no X-CSRFToken header — a guaranteed
        # CSRF failure, sent the way apiClient actually sends requests.
        response = self.client.post(
            reverse("login"),
            data=json.dumps({"username": "x", "password": "y"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        data = json.loads(response.content)
        self.assertIn("refresh", data["error"].lower())

    def test_form_request_gets_friendly_html_page_not_django_default(self):
        response = self.client.post(
            reverse("login"),
            data={"username": "x", "password": "y"},
        )
        self.assertEqual(response.status_code, 403)
        content = response.content.decode()
        # The custom page, not Django's built-in "Forbidden (403)" default.
        self.assertIn("Reload Page", content)
        self.assertNotIn("CSRF verification failed", content)
