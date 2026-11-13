"""
The floor plan in a real browser: the cards, making a bill, cleaning a
table, moving an order, combining and splitting tables, approving a QR
order, renaming and adding tables, a table's QR.

Why: on 13 November 2026 the page's 345 lines of inline script became
modules (static/js/tables/). The module tests (static/js/tests/tables.test.mjs)
prove each piece with fakes; this proves the page, the modules, Bootstrap's
sheets and the server together, then reads the database.

Three things here fail on the old page:
  * the Transfer button in the header opened a sheet whose source table
    could not be chosen, so Execute Transfer did nothing;
  * a name being typed in the Tables sheet was wiped by the 5-second
    refresh;
  * a table's QR was fetched from api.qrserver.com, which was handed the
    table's token and does not answer without internet.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test orders.tests.test_tables_browser
"""
from decimal import Decimal as D

from accounts.models import User
from core.tests.browser import BrowserTestCase
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from tablemerge.models import TableMerge
from tenants.models import Outlet, Tenant

CARD = """
const card = (name) => t.all(".table-card").find((c) => c.querySelector(".table-name").textContent === name);
const tap = (name, action) => {
  const button = card(name).querySelector(`[data-action="${action}"]`);
  if (!button) throw new Error(`${name} has no ${action} button`);
  button.click();
};
const statusOf = (name) => card(name) && card(name).dataset.status;
const toast = () => t.until(() => { const el = document.querySelector(".swal2-toast .swal2-title"); return el && el.textContent; });
const shown = (id) => t.until(() => document.getElementById(id).classList.contains("show"));
const pick = (id, name) => {
  const select = document.getElementById(id);
  const option = Array.from(select.options).find((o) => o.textContent === name);
  if (!option) throw new Error(`${id} does not offer ${name}`);
  select.value = option.value;
};
const offered = (id) => Array.from(document.getElementById(id).options).filter((o) => o.value).map((o) => o.textContent);
await t.until(() => t.all(".table-card").length > 0);
"""
# Wait until the floor has been asked for, and answered, once more.
NEXT_POLL = """
const polls = () => performance.getEntriesByType("resource").filter((e) => e.name.includes("/tables-data/")).length;
const before = polls();
await t.until(() => polls() > before);
await new Promise((r) => setTimeout(r, 300));
"""


class FloorPlanBase(BrowserTestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Salt & Pepper", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.manager = User.objects.create_user(username="floor_manager", password="pw", role="manager",
                                                first_name="Asha", last_name="Rao",
                                                tenant=self.tenant, outlet=self.outlet)
        self.waiter = User.objects.create_user(username="floor_waiter", password="pw", role="waiter",
                                               tenant=self.tenant, outlet=self.outlet)
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                            name="Chilli Chicken", price=D("400"), gst_percentage=D("0"))
        make = lambda name, section, state="free": Table.objects.create(  # noqa: E731
            tenant=self.tenant, outlet=self.outlet, name=name, section=section, state=state)
        self.window = make("Window", "Hall", "ordering")
        self.door = make("Door", "Hall")
        self.patio = make("Patio 1", "Patio")
        self.dirty = make("Corner", "Hall", "cleaning")
        self.order = self.order_at(self.window, "sent", self.manager)

    def order_at(self, table, item_status, user):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open", source="dine_in",
                                     table=table, created_by=user)
        OrderItem.objects.create(order=order, menu_item=self.dish, quantity=1, price=D("400"),
                                 gst_percentage=D("0"), total_price=D("400"), status=item_status)
        order.recalculate_totals()
        return order

    def browse(self, steps, user=None, expect=()):
        """Run the steps on the floor plan. `expect` lists the refusals the
        test sets up on purpose, by address; nothing else may appear."""
        out = self.run_page("/tables/", steps, user=user or self.manager)
        unexpected = list(out["errors"])
        for refusal in expect:
            match = next((e for e in unexpected if refusal in e), None)
            self.assertIsNotNone(match, f"expected a refusal like {refusal!r}; the browser saw {out['errors']}")
            unexpected.remove(match)
        self.assertEqual(unexpected, [], "the browser reported errors:\n" + "\n".join(unexpected))
        return out["results"]


class FloorTest(FloorPlanBase):

    def test_the_floor_is_drawn_and_a_free_table_starts_an_order(self):
        seen = self.browse([
            ("floor", CARD + """
                return {
                  cards: t.all(".table-card").map((c) => [c.querySelector(".table-name").textContent, c.dataset.status]),
                  sections: t.all(".section-header").map((s) => s.textContent.trim()),
                  stats: ["total", "free", "active", "billing"].map((k) => t.text("#stat-" + k)),
                  strip: t.text(".alert-strip"),
                  waiter: card("Window").querySelector(".waiter-badge").textContent,
                  // the stylesheet is there: a class, not a style attribute, colours the legend
                  swatch: getComputedStyle(document.querySelector(".legend-swatch.bg-free")).backgroundColor,
                  inline: t.all("[onclick], #tableGrid [style]").length,
                  heading: t.text(".brand-subtitle .page-name") + " / " + t.text(".brand-subtitle .outlet-name"),
                };
            """),
            ("new_order", CARD + 'tap("Door", "table.go"); return true;', "/billing/"),
            ("landed", "return location.pathname + location.search;"),
        ])
        floor = seen["floor"]
        self.assertEqual(floor["cards"], [["Corner", "cleaning"], ["Door", "free"], ["Window", "preparing"], ["Patio 1", "free"]])
        self.assertEqual(floor["sections"], ["Hall", "Patio"])
        self.assertEqual(floor["stats"], ["4", "2", "1", "0"])
        self.assertIn("All clear", floor["strip"])
        self.assertEqual(floor["waiter"], "AR")
        self.assertEqual(floor["swatch"], "rgb(16, 185, 129)")
        self.assertEqual(floor["inline"], 0)
        self.assertEqual(floor["heading"], "Floor Plan / Main")
        self.assertEqual(seen["landed"], f"/billing/?table={self.door.id}")

    def test_bill_makes_the_bill_and_opens_it(self):
        self.browse([
            ("bill", CARD + 'tap("Window", "table.bill"); return true;', f"/bill/{self.order.id}/"),
        ])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "billing")

    def test_a_dirty_table_is_marked_clean(self):
        seen = self.browse([
            ("clean", CARD + """
                tap("Corner", "table.clean");
                const said = await toast();
                await t.until(() => statusOf("Corner") === "free");
                return { said, free: t.text("#stat-free") };
            """),
        ])
        self.assertEqual(seen["clean"], {"said": "Table Cleaned", "free": "3"})
        self.dirty.refresh_from_db()
        self.assertEqual(self.dirty.state, "free")


class TransferTest(FloorPlanBase):

    def test_an_order_is_moved_from_the_header_button(self):
        """On the old page the source table here was a disabled, empty
        picker and no order was ever set, so Execute Transfer did nothing."""
        seen = self.browse([
            ("transfer", CARD + """
                t.click('[data-bs-target="#transferModal"]');
                await shown("transferModal");
                const from = document.getElementById("transferFrom");
                const offer = { from: offered("transferFrom"), to: offered("transferTo"), disabled: from.disabled };
                pick("transferFrom", "Window");
                pick("transferTo", "Patio 1");
                t.click('[data-action="transfer.run"]');
                const said = await toast();
                await t.until(() => statusOf("Patio 1") !== "free");
                return { offer, said, window: statusOf("Window"), open: document.getElementById("transferModal").classList.contains("show") };
            """),
        ])
        self.assertEqual(seen["transfer"]["offer"], {"from": ["Window"], "to": ["Door", "Patio 1"], "disabled": False})
        self.assertEqual(seen["transfer"]["said"], "Table Transferred")
        self.assertEqual(seen["transfer"]["window"], "free")
        self.order.refresh_from_db()
        self.assertEqual(self.order.table_id, self.patio.id)

    def test_an_order_is_moved_from_the_tables_own_sheet(self):
        seen = self.browse([
            ("transfer", CARD + """
                tap("Window", "table.more");
                await shown("actionModal");
                const sheet = { title: t.text("#actionModalTitle"), buttons: t.all("#actionModalButtons button").map((b) => b.textContent.trim()) };
                t.click('#actionModalButtons [data-action="table.transfer"]');
                await shown("transferModal");
                const from = document.getElementById("transferFrom");
                const source = from.options[from.selectedIndex].textContent;
                pick("transferTo", "Door");
                t.click('[data-action="transfer.run"]');
                await t.until(() => statusOf("Door") !== "free");
                return { sheet, source };
            """),
        ])
        self.assertEqual(seen["transfer"]["sheet"], {"title": "Window", "buttons": [
            "Add Items", "Show Running Order", "Transfer Table", "Generate Bill"]})
        self.assertEqual(seen["transfer"]["source"], "Window")
        self.order.refresh_from_db()
        self.assertEqual(self.order.table_id, self.door.id)


class CombineTest(FloorPlanBase):

    def test_two_tables_are_combined_and_split_again(self):
        seen = self.browse([
            ("combine", CARD + """
                t.click('[data-bs-target="#mergeModal"]');
                await shown("mergeModal");
                pick("mergePrimary", "Door");
                pick("mergeSecondary", "Patio 1");
                t.click('[data-action="merge.run"]');
                const said = await toast();
                await t.until(() => statusOf("Patio 1") === "merged");
                return { said, linked: card("Patio 1").querySelector(".occupancy").textContent.trim(),
                         primary: card("Door").querySelector(".occupancy").textContent.trim() };
            """),
            ("asked", CARD + """
                tap("Door", "table.unmerge");
                await t.until(() => document.querySelector(".swal2-confirm"));
                return t.text(".swal2-title");
            """),
        ])
        self.assertEqual(seen["combine"], {"said": "Tables Combined", "linked": "Linked to Door", "primary": "Merged w/ Patio 1"})
        self.assertEqual(seen["asked"], "Unmerge these tables?")
        # asked, not yet answered: still combined
        self.assertTrue(TableMerge.objects.filter(primary_table=self.door, is_active=True).exists())

        seen = self.browse([
            ("split", CARD + """
                tap("Door", "table.unmerge");
                await t.until(() => document.querySelector(".swal2-confirm"));
                t.click(".swal2-confirm");
                await t.until(() => statusOf("Patio 1") === "free");
                return true;
            """),
        ])
        self.assertFalse(TableMerge.objects.filter(primary_table=self.door, is_active=True).exists())


class QrOrderTest(FloorPlanBase):

    def test_a_guests_qr_order_is_approved_from_the_floor(self):
        guest_order = self.order_at(self.door, "review", None)
        seen = self.browse([
            ("approve", CARD + """
                const before = { status: statusOf("Door"), badge: card("Door").querySelector(".waiter-badge").textContent, strip: t.text(".alert-strip") };
                tap("Door", "table.more");
                await shown("actionModal");
                t.click('#actionModalButtons [data-action="table.approve"]');
                await t.until(() => document.querySelector(".swal2-confirm"));
                t.click(".swal2-confirm");
                await t.until(() => statusOf("Door") !== "needs_approval");
                return { before, open: document.getElementById("actionModal").classList.contains("show") };
            """),
        ])
        self.assertEqual(seen["approve"]["before"]["status"], "needs_approval")
        self.assertEqual(seen["approve"]["before"]["badge"], "QR")
        self.assertIn("1 need approval", seen["approve"]["before"]["strip"])
        self.assertFalse(OrderItem.objects.filter(order=guest_order, status="review").exists())


class ManageTablesTest(FloorPlanBase):

    OPEN = CARD + """
        t.click('[data-bs-target="#manageTablesModal"]');
        await shown("manageTablesModal");
        const rowOf = (name) => t.all("#tblManageList tr").find((r) => r.querySelector('[data-field="name"]').value === name);
    """

    def test_a_name_being_typed_survives_the_refresh_and_is_saved(self):
        seen = self.browse([
            ("rename", self.OPEN + """
                const row = rowOf("Door");
                const field = row.querySelector('[data-field="name"]');
                field.value = "Entrance";
            """ + NEXT_POLL + """
                const kept = field.isConnected && field.value;
                row.querySelector('[data-action="manage.save"]').click();
                const said = await toast();
                await t.until(() => card("Entrance"));
                return { kept, said, open: document.getElementById("manageTablesModal").classList.contains("show") };
            """),
        ])
        # the bug this guards: the 5-second refresh redrew the list and the typed name was gone
        self.assertEqual(seen["rename"], {"kept": "Entrance", "said": "Table saved!", "open": True})
        self.door.refresh_from_db()
        self.assertEqual(self.door.name, "Entrance")

    def test_a_table_is_added(self):
        seen = self.browse([
            ("nameless", self.OPEN + """
                t.click('[data-action="manage.create"]');
                return await toast();
            """),
            ("add", """
                document.getElementById("newTableName").value = "T-12";
                document.getElementById("newTableSection").value = "Patio";
                t.click('[data-action="manage.create"]');
                await t.until(() => t.all(".table-card").length === 5);
                return { cleared: document.getElementById("newTableName").value + document.getElementById("newTableSection").value,
                         rows: t.all("#tblManageList tr").length };
            """),
        ])
        self.assertEqual(seen["nameless"], "Enter table name")
        self.assertEqual(seen["add"], {"cleared": "", "rows": 5})
        added = Table.objects.get(outlet=self.outlet, name="T-12")
        self.assertEqual((added.section, added.tenant_id), ("Patio", self.tenant.id))

    def test_a_waiter_may_not_add_a_table_and_is_told(self):
        seen = self.browse([
            ("refused", self.OPEN + """
                document.getElementById("newTableName").value = "T-12";
                t.click('[data-action="manage.create"]');
                return await toast();
            """),
        ], user=self.waiter, expect=("403 (Forbidden) /manage-table/",))
        self.assertEqual(seen["refused"], "Permission denied")
        self.assertFalse(Table.objects.filter(name="T-12").exists())

    def test_a_tables_qr_is_drawn_on_the_page_and_its_token_stays_here(self):
        seen = self.browse([
            ("qr", self.OPEN + """
                window.qrTexts = [];
                const draw = window.QRCode.toCanvas;
                window.QRCode.toCanvas = (text, ...rest) => { window.qrTexts.push(text); return draw(text, ...rest); };
                rowOf("Door").querySelector('[data-action="manage.qr"]').click();
                await shown("qrModal");
                await t.until(() => t.all("#qrBox canvas").length === 1);
                return {
                  title: t.text("#qrModalTitle"),
                  text: window.qrTexts[0].replace(location.origin, ""),
                  images: t.all("#qrModal img").length,
                  elsewhere: performance.getEntriesByType("resource").filter((e) => !e.name.startsWith(location.origin)).map((e) => e.name),
                  failed: getComputedStyle(document.getElementById("qrFailed")).display,
                };
            """),
        ])
        self.assertEqual(seen["qr"], {
            "title": "Door QR Reference",
            "text": f"/menu/digital-menu/?table_token={self.door.qr_token}",
            "images": 0,
            "elsewhere": [],
            "failed": "none",
        })
