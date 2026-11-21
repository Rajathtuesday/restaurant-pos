"""
A guest's QR dishes, approved or not accepted one by one, in Chromium
(21 Nov 2026).

The floor's More sheet lists each waiting guest dish with a tick and a
cross, at phone and tablet widths; the running order page has the same;
"Not accepted" asks for a reason in a sheet that works inside the floor's
own sheet (typing included); and the guest's phone then lists the dish as
not accepted, with the reason. Plan:
md_files/eli5_qr_approve_each_dish_2026-11-21.html
Run: python manage.py test orders.tests.test_guest_dish_browser
"""
import json
from decimal import Decimal

from django.test import Client
from django.urls import reverse

from accounts.models import User
from core.tests.browser import BrowserTestCase
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from orders.views.public_views import make_order_status_token
from tenants.models import Outlet, Tenant, TenantFeatureOverride

PHONE = {"width": 390, "height": 844}
TABLET = {"width": 768, "height": 1024}

# Every button in `selector` is on screen, at least 44 px, and is what a
# finger at its middle would land on (nothing covers it).
TAPPABLE = """
const buttons = t.all(SELECTOR);
return buttons.length > 0 && buttons.every((b) => {
  const r = b.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return r.width >= 44 && r.height >= 44 && r.left >= 0 && r.right <= innerWidth
    && r.top >= 0 && r.bottom <= innerHeight && hit && b.contains(hit);
});
"""


def tappable(selector):
    return TAPPABLE.replace("SELECTOR", json.dumps(selector))


class GuestDishBrowserTest(BrowserTestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Browser Pub", slug="browser-pub", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        for feature in ("qr_menu", "floor_plan", "running_order"):
            TenantFeatureOverride.objects.update_or_create(tenant=self.tenant, feature=feature,
                                                           defaults={"enabled": True})
        self.waiter = User.objects.create_user(username="br-waiter", password="pw", tenant=self.tenant,
                                               outlet=self.outlet, role="waiter")
        self.captain = User.objects.create_user(username="br-captain", password="pw", tenant=self.tenant,
                                                outlet=self.outlet, role="captain")
        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.dishes = {
            name: MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=cat, name=name,
                                          price=Decimal(price))
            for name, price in (("Paneer Tikka", "220"), ("Kingfisher", "180"), ("Brownie", "150"))
        }
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T4")

    def guest_orders(self):
        resp = Client().post(reverse("create-order"), data=json.dumps({
            "table_token": str(self.table.qr_token), "source": "web",
            "cart": [{"id": self.dishes["Paneer Tikka"].id, "quantity": 1},
                     {"id": self.dishes["Kingfisher"].id, "quantity": 2},
                     {"id": self.dishes["Brownie"].id, "quantity": 1}],
        }), content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return Order.objects.get(id=resp.json()["order_id"])

    def line(self, order, name):
        return order.items.get(menu_item__name=name)

    def statuses(self, order):
        return {i.menu_item.name: i.status for i in order.items.all()}

    # ---- the floor's More sheet --------------------------------------------

    def floor_steps(self, order):
        paneer, brownie = self.line(order, "Paneer Tikka").id, self.line(order, "Brownie").id
        row = lambda i: f'.guest-dish[data-guest-item="{i}"]'  # noqa: E731
        return [
            ("open", f"""
                await t.until(() => document.querySelector('[data-action="table.more"][data-id="{self.table.id}"]'));
                t.click('[data-action="table.more"][data-id="{self.table.id}"]');
                await t.until(() => document.querySelector("#actionModal.show") && t.all(".guest-dish").length === 3);
                await new Promise((r) => setTimeout(r, 400));
                return t.all(".guest-dish-name").map((n) => n.textContent.trim());
            """),
            ("tappable", tappable("#actionModal .guest-dish-btn")),
            ("tick", f"""
                t.click('{row(paneer)} [data-action="table.approveOne"]');
                await t.until(() => !document.querySelector('{row(paneer)}'));
                return t.all(".guest-dish").length;
            """),
            ("cross", f"""
                t.click('{row(brownie)} [data-action="table.refuse"]');
                const popup = await t.until(() => document.querySelector(".swal2-popup.swal2-show"));
                const inside = Boolean(popup.closest("#actionModal"));
                const chips = t.all(".oe-chip").map((c) => c.textContent.trim());
                const box = document.getElementById("oe-other");
                box.focus();
                await new Promise((r) => setTimeout(r, 150));
                const typing = document.activeElement === box;
                t.click('.oe-chip[data-reason="Not available"]');
                t.click(".swal2-confirm");
                await t.until(() => !document.querySelector('{row(brownie)}'));
                return {{ inside, chips, typing, left: t.all(".guest-dish").length }};
            """),
            ("all", """
                t.click('#actionModal [data-action="table.approve"]');
                await t.until(() => document.querySelector(".swal2-confirm") && document.querySelector(".swal2-popup.swal2-show"));
                t.click(".swal2-confirm");
                await t.until(() => !document.querySelector("#actionModal.show"));
                return true;
            """),
        ]

    def check_floor(self, viewport):
        order = self.guest_orders()
        out = self.run_page("/tables/", self.floor_steps(order), user=self.waiter, viewport=viewport)
        self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
        res = out["results"]
        self.assertEqual(sorted(res["open"]), ["1 x Brownie", "1 x Paneer Tikka", "2 x Kingfisher"])
        self.assertTrue(res["tappable"], "every tick and cross is on screen, 44 px, uncovered")
        self.assertEqual(res["tick"], 2, "the approved dish leaves the list, the sheet stays")
        cross = res["cross"]
        self.assertTrue(cross["inside"], "the reason sheet opens inside the floor's sheet")
        self.assertEqual(cross["chips"], ["Not available", "Guest changed mind", "Ordered twice"])
        self.assertTrue(cross["typing"], "the Other reason box can take typing")
        self.assertEqual(cross["left"], 1)
        brownie = self.line(order, "Brownie")
        self.assertEqual((brownie.status, brownie.not_accepted, brownie.void_reason), ("voided", True, "Not available"))
        self.assertEqual(self.statuses(order), {"Paneer Tikka": "sent", "Kingfisher": "sent", "Brownie": "voided"})

    def test_floor_sheet_tick_and_cross_on_a_phone(self):
        self.check_floor(PHONE)

    def test_floor_sheet_tick_and_cross_on_a_tablet(self):
        self.check_floor(TABLET)

    # ---- the running order page --------------------------------------------

    def test_running_order_page_tick_and_cross(self):
        card = lambda i: f'.item-card[data-item="{i}"]'  # noqa: E731
        for viewport in (PHONE, TABLET):
            with self.subTest(width=viewport["width"]):
                # Each width its own table: a second QR order at one table
                # joins the first.
                self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name=f"W{viewport['width']}")
                fresh = self.guest_orders()
                paneer, brownie = self.line(fresh, "Paneer Tikka").id, self.line(fresh, "Brownie").id
                out = self.run_page(f"/order/{fresh.id}/", [
                    ("draw", """
                        await t.until(() => t.all(".ro-guest").length === 6);
                        return { bar: t.text(".ro-guest-bar span"), badges: t.all(".status-badge").map((b) => b.textContent.trim()) };
                    """),
                    ("tappable", tappable(".ro-guest")),
                    ("tick", f"""
                        t.click('{card(paneer)} [data-action="item.approve"]');
                        await t.until(() => !document.querySelector('{card(paneer)} .ro-guest'));
                        return t.all(".ro-guest").length;
                    """),
                    ("cross", f"""
                        t.click('{card(brownie)} [data-action="item.refuse"]');
                        await t.until(() => document.querySelector(".swal2-popup.swal2-show .oe-chip"));
                        t.click('.oe-chip[data-reason="Ordered twice"]');
                        t.click(".swal2-confirm");
                        await t.until(() => document.querySelector('{card(brownie)} .void-reason'));
                        return t.text('{card(brownie)} .void-reason');
                    """),
                ], user=self.captain, viewport=viewport)
                self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
                res = out["results"]
                self.assertEqual(res["draw"]["bar"], "3 guest dishes waiting")
                self.assertIn("needs approval", [b.lower() for b in res["draw"]["badges"]])
                self.assertTrue(res["tappable"])
                self.assertEqual(res["tick"], 4)
                self.assertEqual(res["cross"], "Not accepted: Ordered twice")
                self.assertEqual(self.statuses(fresh)["Paneer Tikka"], "sent")
                self.assertTrue(self.line(fresh, "Brownie").not_accepted)

    # ---- the guest's phone -------------------------------------------------

    def test_the_guests_phone_lists_the_dish_not_accepted(self):
        order = self.guest_orders()
        staff = Client()
        staff.force_login(self.waiter)
        staff.post(reverse("refuse-item", args=[self.line(order, "Brownie").id]),
                   data=json.dumps({"reason": "Not available"}), content_type="application/json")
        token = str(self.table.qr_token)
        menu = f"/menu/digital-menu/?table_token={token}"
        out = self.run_page(menu, [
            ("keep", f"""
                localStorage.setItem("rasova_order_status_{token}", {json.dumps(make_order_status_token(order.id))});
                localStorage.setItem("rasova_order_{token}", "{order.id}");
                location.reload();
                return true;
            """, {"selector": "#orderStatusPill"}),
            ("sheet", """
                await t.until(() => document.getElementById("statusSheetItems").textContent.includes("Not accepted"));
                return t.all("#statusSheetItems .status-sheet-item-row").map((r) => r.textContent.replace(/\\s+/g, " ").trim());
            """),
        ], viewport={**PHONE, "mobile": True})
        self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
        rows = out["results"]["sheet"]
        self.assertIn("Not accepted: 1x Brownie Not available", rows)
        self.assertTrue(any(r.startswith("1x Paneer Tikka") for r in rows), rows)
