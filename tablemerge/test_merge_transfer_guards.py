"""
Merging and transferring tables never leaves two orders on one table, a
table in two merges, or an order stranded off its party's bill
(19 Nov 2026).

Found when checking the floor plan's merge and transfer:
- a transfer locked the order but not the table it moved to, so two orders
  moved to the same free table at the same moment both passed the
  "occupied" check;
- moving the order off a merge's main table set that table free and left
  the merged tables showing occupied with no order;
- a table already in one merge could be put in a second one;
- a table merged in with its own open order kept it, off the main table's
  bill.
Run: python manage.py test tablemerge.test_merge_transfer_guards
"""
import json
from threading import Barrier, Thread

from django.db import connection
from django.test import Client, TestCase, TransactionTestCase

from core.tests.test_tenant_walk import Restaurant
from orders.models import Order, Table
from tablemerge.models import TableMerge


def post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


class MergeGuardsTest(TestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "merge-guard")
        self.t3 = Table.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name="Alpha Table 3")
        self.client = Client()
        self.client.force_login(self.r.owner)

    def merge(self, primary, *tables):
        return post(self.client, "/merge-tables/", {"primary_table": primary.id, "tables": [t.id for t in tables]})

    def test_a_table_already_in_a_merge_cannot_join_another(self):
        self.assertEqual(self.merge(self.r.table, self.r.table2).status_code, 200)
        again = self.merge(self.t3, self.r.table2)
        self.assertEqual(again.status_code, 400, again.content)
        self.assertIn("already merged", again.json()["error"])
        self.assertEqual(TableMerge.objects.filter(is_active=True).count(), 1)

    def test_a_merge_main_table_cannot_join_another_merge(self):
        self.merge(self.r.table, self.r.table2)
        self.assertEqual(self.merge(self.t3, self.r.table).status_code, 400)

    def test_a_table_with_its_own_open_order_is_not_merged_in(self):
        Order.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, table=self.t3, status="open",
                             source="dine_in", created_by=self.r.owner)
        resp = self.merge(self.r.table, self.t3)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("own order", resp.json()["error"])
        self.assertFalse(TableMerge.objects.filter(is_active=True).exists())

    def test_free_tables_still_merge(self):
        resp = self.merge(self.r.table, self.r.table2, self.t3)
        self.assertEqual(resp.status_code, 200, resp.content)


class TransferGuardsTest(TestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "transfer-guard")
        self.t3 = Table.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name="Alpha Table 3")
        self.client = Client()
        self.client.force_login(self.r.owner)

    def test_the_order_of_a_merges_main_table_is_not_moved_away(self):
        post(self.client, "/merge-tables/", {"primary_table": self.r.table.id, "tables": [self.r.table2.id]})
        resp = post(self.client, "/transfer-table/", {"order_id": self.r.order.id, "table_id": self.t3.id})
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("merged", resp.json()["error"])
        self.r.order.refresh_from_db()
        self.assertEqual(self.r.order.table_id, self.r.table.id)

    def test_a_plain_transfer_still_works(self):
        resp = post(self.client, "/transfer-table/", {"order_id": self.r.order.id, "table_id": self.t3.id})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.r.order.refresh_from_db()
        self.assertEqual(self.r.order.table_id, self.t3.id)


class TransferRaceTest(TransactionTestCase):
    """Two waiters move two different orders to the same free table at the
    same moment. Real threads, real connections."""

    def test_only_one_order_lands_on_the_table(self):
        r = Restaurant("Alpha", "transfer-race")
        target = Table.objects.create(tenant=r.tenant, outlet=r.outlet, name="Alpha Table 9")
        second = Order.objects.create(tenant=r.tenant, outlet=r.outlet, table=r.table2, status="open",
                                      source="dine_in", created_by=r.owner)
        orders = [r.order.id, second.id]
        results = [None, None]
        start = Barrier(2)

        def one(i):
            try:
                client = Client()
                client.force_login(r.owner)
                start.wait(timeout=10)
                results[i] = post(client, "/transfer-table/", {"order_id": orders[i], "table_id": target.id})
            except Exception as e:  # noqa: BLE001 -- recorded for the assertion
                results[i] = f"ERROR: {e}"
            finally:
                connection.close()

        threads = [Thread(target=one, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([x for x in results if isinstance(x, str)], [])
        self.assertEqual(sorted(x.status_code for x in results), [200, 400], [x.content for x in results])
        self.assertEqual(Order.objects.filter(table=target, status__in=["open", "billing"]).count(), 1)
