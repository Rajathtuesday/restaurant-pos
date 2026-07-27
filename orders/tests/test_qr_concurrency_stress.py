"""
Stress tests for N guests ordering via the same QR at (as close to) the
same moment -- hitting the real create-order HTTP endpoint with actual
concurrent threads, not calling internal service functions directly.
Existing concurrency tests (tokens/tests.py::TokenStressTests etc.)
already proved token-number generation itself is race-safe under load;
this proves (or disproves) the WHOLE guest-facing flow is, end to end,
for both ways a QR gets shared:

  - A table QR: several DIFFERENT people/devices at one physical table,
    each placing their own first order (no stored order_id yet).
  - The outlet-wide counter QR: several DIFFERENT customers who must
    never be merged into each other's orders.

Run: python manage.py test orders.tests.test_qr_concurrency_stress
"""
import json
from decimal import Decimal
from threading import Thread

from django.db import connection
from django.test import Client, TransactionTestCase

from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from tenants.models import Tenant, Outlet


class _Base(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Stress QR Cafe", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Snacks")
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Samosa", price=Decimal("30.00"),
        )

    def _fire(self, n, payload_fn):
        """Run n concurrent create-order POSTs, each with its own Client
        and DB connection (Django connections aren't thread-safe to share)."""
        results = [None] * n

        def _one(idx):
            try:
                client = Client()
                resp = client.post(
                    "/create-order/",
                    data=json.dumps(payload_fn(idx)),
                    content_type="application/json",
                )
                results[idx] = resp
            except Exception as e:  # noqa: BLE001 -- record for assertion
                results[idx] = f"ERROR: {e}"
            finally:
                connection.close()

        threads = [Thread(target=_one, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results


class SameTableConcurrentFirstOrderTest(_Base):
    """
    Several guests at ONE physical table, each on their own phone, each
    scanning the same table QR and placing their own FIRST order (none of
    them have a stored order_id -- that's the realistic case, since
    order_id lives in each guest's own browser localStorage, not shared
    across devices at the same table).
    """

    def setUp(self):
        super().setUp()
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")

    def test_n_simultaneous_first_orders_at_one_table(self):
        n = 8
        results = self._fire(n, lambda idx: {
            "table_token": str(self.table.qr_token),
            "cart": [{"id": self.item.id, "quantity": 1}],
            "source": "web",
        })

        thread_errors = [r for r in results if isinstance(r, str)]
        self.assertEqual(thread_errors, [], f"Thread-level exceptions: {thread_errors}")

        statuses = [r.status_code for r in results]
        bodies = [r.json() for r in results]

        self.assertTrue(
            all(s == 200 for s in statuses),
            f"Expected every guest's first order to succeed. Statuses: {statuses}, bodies: {bodies}"
        )

        order_ids = {b["order_id"] for b in bodies if b.get("success")}
        self.assertEqual(
            len(order_ids), 1,
            f"Expected all {n} guests at the same table to land on ONE shared "
            f"order, got {len(order_ids)} distinct order(s): {order_ids}"
        )

        order = Order.objects.get(id=order_ids.pop())
        self.assertEqual(
            order.items.count(), n,
            f"Expected all {n} guests' items on the one merged order, found {order.items.count()}."
        )


class CounterQRConcurrentDifferentCustomersTest(_Base):
    """
    Several DIFFERENT customers at a tableless counter, all scanning the
    SAME outlet-wide QR at once. Must NEVER merge -- the counter QR is
    shared by everyone, so unlike a table there's no physical boundary
    that makes merging safe or even meaningful.
    """

    def test_n_simultaneous_counter_orders_never_merge(self):
        n = 8
        results = self._fire(n, lambda idx: {
            "table_token": str(self.outlet.qr_token),
            "cart": [{"id": self.item.id, "quantity": 1}],
            "source": "web",
        })

        thread_errors = [r for r in results if isinstance(r, str)]
        self.assertEqual(thread_errors, [], f"Thread-level exceptions: {thread_errors}")

        statuses = [r.status_code for r in results]
        bodies = [r.json() for r in results]
        self.assertTrue(all(s == 200 for s in statuses), f"Statuses: {statuses}, bodies: {bodies}")

        order_ids = [b["order_id"] for b in bodies]
        self.assertEqual(
            len(set(order_ids)), n,
            f"Expected {n} separate orders (one per customer), got {len(set(order_ids))}: {order_ids}"
        )

        # Every one of those orders must have gotten its own, distinct token number.
        from tokens.models import TokenOrder
        token_numbers = list(
            TokenOrder.objects.filter(order_id__in=order_ids).values_list("token_number", flat=True)
        )
        self.assertEqual(len(token_numbers), n)
        self.assertEqual(len(set(token_numbers)), n, f"Duplicate token numbers: {token_numbers}")
