"""
The owner's dashboard in a real browser (V6): today first, the refresh
drawing the server's numbers, the Zomato switch, a direct-billing counter's
New Order, and the superuser's own group.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test accounts.tests.test_dashboard_browser
"""
from accounts.models import User
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from setup.models import AggregatorConfig
from tenants.models import TenantFeatureOverride


class DashboardBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "dashb-alpha")

    def run_clean(self, steps, user=None):
        out = self.run_page("/dashboard/", steps, user=user or self.r.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        return out["results"]

    def test_today_is_first_and_the_refresh_draws_the_servers_numbers(self):
        seen = self.run_clean([
            ("first", '''const main = document.querySelector(".dash");
                         return main.firstElementChild.id;'''),
            ("refresh", '''const revenue = document.querySelector('#today [data-field="revenue"]');
                           const before = revenue.textContent;
                           revenue.textContent = "stale";
                           t.click('[data-action="dashboard.refresh"]');
                           await t.until(() => revenue.textContent !== "stale");
                           return { before, after: revenue.textContent,
                                    said: t.text('#today [data-field="updated"]') };'''),
            ("groups", 'return t.all(".dash-group h2").map((h) => h.id);'),
        ])
        self.assertEqual(seen["first"], "today")
        self.assertEqual(seen["refresh"]["after"], seen["refresh"]["before"])
        self.assertTrue(seen["refresh"]["after"].startswith("₹"))
        self.assertRegex(seen["refresh"]["said"], r"^Updated \d+:\d\d$")
        self.assertEqual(seen["groups"], ["group-service", "group-office"])

    def test_the_zomato_switch_turns_on_without_a_reload(self):
        seen = self.run_clean([
            ("switch", '''const button = document.querySelector('[data-platform="zomato"]');
                          window.marker = "same page";
                          button.click();
                          await t.until(() => button.classList.contains("is-on"));
                          return { pressed: button.getAttribute("aria-pressed"), samePage: window.marker };'''),
        ])
        self.assertEqual(seen["switch"], {"pressed": "true", "samePage": "same page"})
        config = AggregatorConfig.objects.get(tenant=self.r.tenant, outlet=self.r.outlet)
        self.assertTrue(config.zomato_enabled)

    def test_the_superuser_sees_rasovas_tools_in_their_own_group(self):
        boss = User.objects.create_superuser(username="dash-boss", password="pw", tenant=self.r.tenant,
                                             outlet=self.r.outlet, role="owner")
        seen = self.run_clean([("staff", 'return t.all(".dash-group-staff .tile-label").map((e) => e.textContent);')],
                              user=boss)
        self.assertEqual(seen["staff"], ["Partner", "Features", "Admin"])


class DirectBillingTest(BrowserTestCase):

    def test_new_order_opens_the_bill_of_a_new_token(self):
        r = Restaurant("Beta", "dashb-beta")
        r.tenant.tenant_type = "cafe"
        r.tenant.save(update_fields=["tenant_type"])
        TenantFeatureOverride.objects.update_or_create(tenant=r.tenant, feature="direct_billing_mode",
                                                       defaults={"enabled": True})
        out = self.run_page("/dashboard/", [
            ("tap", 't.click(\'[data-action="dashboard.new-order"]\');', "/token/"),
            ("landed", "return location.pathname;"),
        ], user=r.owner)
        self.assertEqual(out["errors"], [])
        self.assertRegex(out["results"]["landed"], r"^/token/\d+/bill/$")
