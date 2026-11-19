"""
A merge can always be undone from the floor, from any table in it
(19 Nov 2026).

Found on the demo floor: merges made before the 19 Nov guards had tangled
(Table 2 the main table of four merges, then Table 2 merged into Table 1).
Every card showed "Linked", Unmerge shows only on a main table's card, and
the server ended one merge per tap, found by its main table alone: no
button on the floor could undo it.

Now Unmerge takes any table in a merge, main or joined, and ends every
active merge connected to it, so one tap leaves each of those tables on
its own. A merge not connected to that table is left alone.
Run: python manage.py test tablemerge.test_unmerge_any_table
"""
from django.test import Client, TestCase

from core.tests.test_tenant_walk import Restaurant
from orders.models import Table
from tablemerge.models import TableMerge


class UnmergeAnyTableTest(TestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "unmerge-any")
        t = lambda name: Table.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name=name)  # noqa: E731
        self.t1, self.t2 = self.r.table, self.r.table2
        self.t3, self.t4, self.t5, self.t6 = t("Alpha Table 3"), t("Alpha Table 4"), t("Alpha Table 5"), t("Alpha Table 6")
        self.client = Client()
        self.client.force_login(self.r.owner)

    def merge(self, primary, *tables):
        """Made directly: the 19 Nov guards refuse these through the service,
        but floors merged before them still hold such rows."""
        m = TableMerge.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, primary_table=primary,
                                      created_by=self.r.owner)
        m.tables.set(tables)
        return m

    def unmerge(self, table):
        return self.client.post(f"/unmerge-tables/{table.id}/")

    def active(self):
        return set(TableMerge.objects.filter(is_active=True).values_list("id", flat=True))

    def test_the_demo_floor_tangle_comes_apart_in_one_tap_from_a_joined_table(self):
        for joined in (self.t1, self.t3, self.t4, self.t5):
            self.merge(self.t2, joined)
        self.merge(self.t1, self.t2)
        Table.objects.filter(pk=self.t1.pk).update(state="ordering")   # it holds the fixture's open order
        resp = self.unmerge(self.t4)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.active(), set())
        # Table 1 was "joined" to Table 2, which has no order: it keeps its own.
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.state, "ordering")

    def test_a_joined_table_can_end_its_merge(self):
        m = self.merge(self.t3, self.t4)
        self.assertEqual(self.unmerge(self.t4).status_code, 200)
        m.refresh_from_db()
        self.assertFalse(m.is_active)
        self.t4.refresh_from_db()
        self.assertEqual(self.t4.state, "free")

    def test_a_main_table_of_several_merges_ends_them_all(self):
        for joined in (self.t3, self.t4, self.t5):
            self.merge(self.t6, joined)
        self.assertEqual(self.unmerge(self.t6).status_code, 200)
        self.assertEqual(self.active(), set())

    def test_a_merge_not_connected_to_the_table_is_left_alone(self):
        self.merge(self.t3, self.t4)
        other = self.merge(self.t5, self.t6)
        self.unmerge(self.t3)
        self.assertEqual(self.active(), {other.id})

    def test_a_table_in_no_merge_answers_not_found(self):
        self.assertEqual(self.unmerge(self.t6).status_code, 404)

    def test_another_restaurants_merge_is_not_reached(self):
        b = Restaurant("Beta", "unmerge-any-b")
        theirs = TableMerge.objects.create(tenant=b.tenant, outlet=b.outlet, primary_table=b.table, created_by=b.owner)
        theirs.tables.set([b.table2])
        self.assertEqual(self.unmerge(b.table2).status_code, 404)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_active)
