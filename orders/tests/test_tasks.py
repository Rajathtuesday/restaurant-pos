"""
Tests for orders/tasks.py::print_kot_task.

This Celery task had zero test coverage anywhere in the repo (confirmed by
grep for the task name across orders/tests/ before Phase 3 of the orders
app split). It's worth covering now specifically because the move relocated
KOTBatch to kitchen.models — this proves the task still resolves
kitchen.models.KOTBatch correctly end-to-end after that move.

Run: python manage.py test orders.tests.test_tasks
"""
from unittest.mock import patch

from django.test import TestCase

from kitchen.models import KOTBatch
from orders.models import Order
from orders.tasks import print_kot_task
from setup.models import KitchenStation
from tenants.models import Outlet, Tenant


class PrintKotTaskTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Print Task Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.station = KitchenStation.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Grill",
            printer_ip="192.168.1.50",
        )
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        self.kot = KOTBatch.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=self.order,
            kot_number=1, station=self.station,
        )

    @patch("orders.tasks.PrintingService")
    def test_resolves_kitchen_kotbatch_and_prints(self, mock_service_cls):
        mock_service = mock_service_cls.return_value
        mock_service.print_kot.return_value = True

        result = print_kot_task(self.station.id, self.order.id, self.kot.id)

        self.assertTrue(result)
        mock_service.print_kot.assert_called_once_with(self.order, self.kot)

    @patch("orders.tasks.PrintingService")
    def test_missing_kotbatch_returns_false_without_raising(self, mock_service_cls):
        result = print_kot_task(self.station.id, self.order.id, self.kot.id + 999)

        self.assertFalse(result)
        mock_service_cls.return_value.print_kot.assert_not_called()
