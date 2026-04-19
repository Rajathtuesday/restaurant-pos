import os
import django
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from tenants.models import Tenant, Outlet
from accounts.models import User
from orders.models import Order, OrderItem, Payment, Table
from menu.models import MenuItem, MenuCategory
from decimal import Decimal
from django.db import transaction
from orders.services.payment_service import process_payment
from orders.utils.payment_utils import validate_order_payment

tenant = Tenant.objects.first()
if not tenant:
    tenant = Tenant.objects.create(name="Test Tenant")

outlet = Outlet.objects.filter(tenant=tenant).first()
if not outlet:
    outlet = Outlet.objects.create(tenant=tenant, name="Test Outlet")

user = User.objects.filter(tenant=tenant).first()
if not user:
    user = User.objects.create_user(username="testuser", password="123", role="owner", tenant=tenant, outlet=outlet)

cat = MenuCategory.objects.filter(tenant=tenant).first()
if not cat:
    cat = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Test Cat")

item = MenuItem.objects.filter(tenant=tenant).first()
if not item:
    item = MenuItem.objects.create(tenant=tenant, outlet=outlet, category=cat, name="Test Item", price=Decimal("100.00"), gst_percentage=Decimal("5.00"))

table = Table.objects.filter(tenant=tenant).first()
if not table:
    table = Table.objects.create(tenant=tenant, outlet=outlet, name="Test Table")

# Create Order
order = Order.objects.create(tenant=tenant, outlet=outlet, table=table, status="billing")
OrderItem.objects.create(order=order, menu_item=item, quantity=1, price=item.price, gst_percentage=item.gst_percentage, total_price=item.price * Decimal("1.05"))
order.recalculate_totals()
print(f"Order {order.id} Grand Total: {order.grand_total}")

# Try to pay
try:
    with transaction.atomic():
        print(f"Applying payment of {order.grand_total}")
        res = process_payment(order, "cash", order.grand_total, user)
        print("process_payment result:", res)
        order.refresh_from_db()
        print("Order status:", order.status)
        if order.status == "paid":
            validate_order_payment(order)
            order.status = "closed"
            order.save()
            print("Order closed successfully!")
except Exception as e:
    print("EXCEPTION CAUGHT:", repr(e))
    import traceback
    traceback.print_exc()

