# reports/tests/test_audit_report.py
"""
Hand-calculated regression tests for discount_void_audit().

Run: python manage.py test reports.tests.test_audit_report
"""
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from core.utils import get_business_date
from orders.models import Order, OrderEvent
from tenants.models import Outlet, Tenant


class DiscountVoidAuditTest(TestCase):
    """
    staff_a: 2 new-shape order discounts, 1 comp, 2 voids ("wrong order").
    staff_b: 1 OLD-shape order discount (event_type="status_changed",
             metadata__action="discount_applied" -- proves the union query
             still picks up pre-fix data), 1 unrelated status_changed event
             that must NOT be counted as a discount, 1 item discount,
             1 void ("customer changed mind").
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Audit Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.staff_a = User.objects.create_user(
            username="audit_staff_a", password="pw", role="manager",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.staff_b = User.objects.create_user(
            username="audit_staff_b", password="pw", role="manager",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, created_by=self.staff_a, status="open",
        )
        self.today = get_business_date(timezone.now(), self.outlet)

        def _event(event_type, metadata, staff):
            OrderEvent.objects.create(
                tenant=self.tenant, outlet=self.outlet, order=self.order,
                event_type=event_type, metadata=metadata, created_by=staff,
            )

        # New-shape order discounts -- staff_a x2
        _event("discount_applied", {"action": "discount_applied"}, self.staff_a)
        _event("discount_applied", {"action": "discount_applied"}, self.staff_a)
        # Old-shape order discount -- staff_b x1 (pre-fix data, must still count)
        _event("status_changed", {"action": "discount_applied"}, self.staff_b)
        # A status_changed event that is NOT a discount -- must NOT be counted
        _event("status_changed", {"action": "something_unrelated"}, self.staff_b)

        # Item-level discount -- staff_b x1
        _event("item_discount_applied", {"action": "item_discount_applied"}, self.staff_b)

        # Complimentary -- staff_a x1
        _event("item_complimentary", {"item_id": 1}, self.staff_a)

        # Voids -- staff_a x2 "wrong order", staff_b x1 "customer changed mind"
        _event("item_voided", {"item": "X", "reason": "wrong order"}, self.staff_a)
        _event("item_voided", {"item": "Y", "reason": "wrong order"}, self.staff_a)
        _event("item_voided", {"item": "Z", "reason": "customer changed mind"}, self.staff_b)

    def _report(self):
        from reports.services.audit_reports import discount_void_audit
        return discount_void_audit(self.tenant, self.outlet, self.today, self.today)

    def test_order_discounts_union_old_and_new_shape(self):
        counts = {r["created_by__username"]: r["count"] for r in self._report()["discounts"]}
        self.assertEqual(counts["audit_staff_a"], 2)
        self.assertEqual(counts["audit_staff_b"], 1)  # old-shape event counted too

    def test_unrelated_status_changed_event_not_counted_as_discount(self):
        total = sum(r["count"] for r in self._report()["discounts"])
        self.assertEqual(total, 3)  # 2 new + 1 old, NOT 4

    def test_item_discounts(self):
        counts = {r["created_by__username"]: r["count"] for r in self._report()["item_discounts"]}
        self.assertEqual(counts, {"audit_staff_b": 1})

    def test_comps(self):
        counts = {r["created_by__username"]: r["count"] for r in self._report()["comps"]}
        self.assertEqual(counts, {"audit_staff_a": 1})

    def test_voids_by_staff(self):
        counts = {r["created_by__username"]: r["count"] for r in self._report()["voids"]}
        self.assertEqual(counts["audit_staff_a"], 2)
        self.assertEqual(counts["audit_staff_b"], 1)

    def test_void_reasons_breakdown(self):
        counts = {r["metadata__reason"]: r["count"] for r in self._report()["void_reasons"]}
        self.assertEqual(counts["wrong order"], 2)
        self.assertEqual(counts["customer changed mind"], 1)

    def test_cross_tenant_isolation(self):
        other_tenant = Tenant.objects.create(name="Other Audit Tenant")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Other Main")
        from reports.services.audit_reports import discount_void_audit
        report = discount_void_audit(other_tenant, other_outlet, self.today, self.today)
        self.assertEqual(report["discounts"], [])
        self.assertEqual(report["voids"], [])
