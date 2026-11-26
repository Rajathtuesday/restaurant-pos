"""
Links that lead somewhere: the owner's dashboard and the report tabs.

Why (26 Nov 2026, item 7 of md_files/rasova_fault_fix_plan_2026-11-26.md):
the dashboard said "Rs 3,772.50 given in discounts" (and "N voids today"),
linking to the Discount/Void Audit, and the report tabs showed Menu
Engineering, Labor Cost, the Audit and CRM Analytics. Those need the
advanced_reports feature, which is off by default for every restaurant, so
all four answered the owner with a 403 that told him to "contact your
restaurant manager". Now a link shows only where its page opens (the same
checks the page makes, core.decorators.may), the 403 page says why, and
pubs get the Audit by default (the owner's decision D2: it is the leak
report a pub owner needs most).

Run: python manage.py test reports.tests.test_report_links
"""
import re
from decimal import Decimal

from django.test import Client, TestCase

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order
from tenants.models import Outlet, Tenant, TenantFeatureOverride

NOT_FOLLOWED = ("/logout/", "/live-demo/", "/demo", "/static/", "/media/", "/admin/")
ADVANCED = ("/reports/menu-engineering/", "/reports/labor/", "/reports/audit/", "/reports/crm-analytics/")


class ReportLinksTest(TestCase):

    def owner_of(self, tenant_type, slug, **features):
        tenant = Tenant.objects.create(name=f"{slug} place", slug=slug, tenant_type=tenant_type)
        for feature, on in features.items():
            TenantFeatureOverride.objects.create(tenant=tenant, feature=feature, enabled=on)
        outlet = Outlet.objects.create(tenant=tenant, name="Main")
        owner = User.objects.create_user(username=f"{slug}-owner", password="pw", role="owner",
                                         tenant=tenant, outlet=outlet)
        category = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Food")
        MenuItem.objects.create(tenant=tenant, outlet=outlet, category=category, name="Dal",
                                price=Decimal("200"), gst_percentage=Decimal("5"))
        # Today: a settled bill with a discount, and a void.
        Order.objects.create(tenant=tenant, outlet=outlet, created_by=owner, status="closed",
                             source="dine_in", grand_total=Decimal("180"), discount_total=Decimal("20"))
        Order.objects.create(tenant=tenant, outlet=outlet, created_by=owner, status="cancelled", source="dine_in")
        client = Client()
        client.force_login(owner)
        return client

    def links(self, client, url):
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200, url)
        found = set(re.findall(r'href="(/[^"#?]*)', resp.content.decode()))
        return sorted(link for link in found if not link.startswith(NOT_FOLLOWED))

    def refused(self, client, links):
        return [link for link in links if client.get(link).status_code == 403]

    def tabs(self, client):
        html = client.get("/reports/dashboard/").content.decode()
        return re.findall(r'<a href="(/reports/[^"]+)" class="report-tab', html)

    def test_every_link_on_the_dashboard_opens_for_a_fine_dining_owner(self):
        client = self.owner_of("fine_dining", "links-fd")
        page = client.get("/dashboard/")
        self.assertContains(page, "given in discounts")
        self.assertContains(page, "1 void today")
        self.assertEqual(self.refused(client, self.links(client, "/dashboard/")), [])

    def test_every_report_tab_opens_for_a_fine_dining_owner(self):
        client = self.owner_of("fine_dining", "tabs-fd")
        tabs = self.tabs(client)
        self.assertIn("/reports/dashboard/", tabs)
        self.assertEqual(self.refused(client, tabs), [])
        self.assertEqual([t for t in tabs if t in ADVANCED], [])

    def test_with_advanced_reports_the_tabs_show_and_open(self):
        client = self.owner_of("fine_dining", "tabs-adv", advanced_reports=True, crm=True)
        tabs = self.tabs(client)
        self.assertEqual([t for t in ADVANCED if t not in tabs], [])
        self.assertEqual(self.refused(client, tabs), [])

    def test_a_pub_owner_gets_the_audit(self):
        client = self.owner_of("pub", "links-pub")
        self.assertEqual(client.get("/reports/audit/").status_code, 200)
        self.assertIn("/reports/audit/", self.tabs(client))
        self.assertContains(client.get("/dashboard/"), 'href="/reports/audit/"')
        # Only the Audit: the other advanced reports stay off for a pub.
        self.assertEqual(client.get("/reports/labor/").status_code, 403)

    def test_the_403_page_says_why(self):
        client = self.owner_of("fine_dining", "says-why")
        resp = client.get("/reports/labor/")
        self.assertContains(resp, "not switched on for your restaurant", status_code=403)
        self.assertNotContains(resp, "contact your restaurant manager", status_code=403)
