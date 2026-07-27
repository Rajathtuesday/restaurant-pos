"""
Confirms create_order's rate limit actually returns the JSON 429 it looks
like it returns.

Background: with block=True (the original setting), django_ratelimit
raises Ratelimited (a PermissionDenied subclass) BEFORE the view body ever
runs -- create_order's own `if getattr(request, "limited", False): return
JsonResponse(..., status=429)` was unreachable dead code. No
RATELIMIT_EXCEPTION_CLASS override and no custom handler403 exist anywhere
in the repo, so a real rate-limit hit fell through to Django's default
PermissionDenied handling (renders 403.html as HTML) instead of the JSON
429 an API/fetch() caller expects. Fixed by switching to block=False, same
pattern already proven correct in accounts/views/auth_views.py::login_view.

Confirmed by grep before this file existed: no test anywhere in the repo
asserted an actual 429 from any rate limiter -- this is the first.

Run: python manage.py test orders.tests.test_create_order_ratelimit
"""
import json
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from menu.models import MenuCategory, MenuItem
from tenants.models import Tenant, Outlet


@override_settings(RATELIMIT_ENABLE=True)
class CreateOrderRateLimitTest(TestCase):
    """
    RATELIMIT_ENABLE = not _TESTING disables rate limiting entirely under
    the test runner -- explicitly re-enabled here. django_ratelimit's
    counters live in the cache, which is NOT rolled back between TestCase
    methods the way the DB is, so cache.clear() in setUp/tearDown is
    required or one test's hits count toward the next.
    """

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Rate Limit Order Cafe", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Snacks"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Vada", price=Decimal("20.00"),
        )

    def tearDown(self):
        cache.clear()

    def _post(self):
        payload = {
            "table_token": str(self.outlet.qr_token),
            "cart": [{"id": self.item.id, "quantity": 1}],
            "source": "web",
        }
        return self.client.post(
            reverse("create-order"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_returns_a_real_json_429_once_the_limit_is_hit(self):
        for _ in range(20):
            resp = self._post()
            self.assertEqual(resp.status_code, 200)

        resp = self._post()
        # This is the actual bug this test guards against: with the old
        # block=True setting, this would have been a 403 rendering
        # 403.html as HTML, not a JSON 429 -- both the status code AND
        # that the body is real, parseable JSON matter here.
        self.assertEqual(resp.status_code, 429)
        self.assertIn("error", resp.json())
