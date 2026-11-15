"""
One draft purchase order per supplier (unique_draft_po_per_supplier).

Why (15 Nov 2026): a second New PO for a supplier who already had a draft
reached the database, which refused it, and the owner got a 500 with no
words. It is a refusal now that names the draft to add to.
Run: python manage.py test inventory.tests.test_po_one_draft
"""
import json

from django.test import Client, TestCase

from core.tests.test_tenant_walk import Restaurant
from inventory.models import PurchaseOrder


class OneDraftPerSupplierTest(TestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "po-draft")
        self.client = Client()
        self.client.force_login(self.r.owner)

    def create(self):
        return self.client.post("/inventory/purchase-orders/create/", content_type="application/json", data=json.dumps({
            "supplier_id": self.r.supplier.id, "notes": "",
            "items": [{"item_id": self.r.inventory_item.id, "quantity": 3, "unit_price": 250}],
        }))

    def test_a_second_draft_is_refused_in_words(self):
        resp = self.create()          # the fixture already holds a draft for this supplier
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already has a draft PO", resp.json()["error"])
        self.assertEqual(resp.json()["po_id"], self.r.purchase_order.id)
        self.assertEqual(PurchaseOrder.objects.filter(tenant=self.r.tenant).count(), 1)

    def test_once_the_draft_is_ordered_a_new_one_is_made(self):
        PurchaseOrder.objects.filter(pk=self.r.purchase_order.pk).update(status="ordered")
        resp = self.create()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(PurchaseOrder.objects.filter(tenant=self.r.tenant, status="draft").count(), 1)
