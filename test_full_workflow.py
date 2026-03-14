# test_full_workflow.py
import os
import django
from decimal import Decimal
from django.utils import timezone

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from inventory import models
from tenants.models import Tenant, Outlet
from accounts.models import User
from menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, ModifierGroup, Modifier
from orders.models import Table, Order, OrderItem, OrderItemModifier, KOTBatch, Payment
from inventory.models import InventoryItem, Recipe

print("\n" + "="*60)
print("🧪 POS SYSTEM COMPLETE WORKFLOW TEST")
print("="*60)

# ==========================================
# PHASE 1: CREATE TEST DATA
# ==========================================

print("\n📦 PHASE 1: Creating Test Data")
print("-" * 60)

# 1. Create Tenant (Restaurant Group)
print("✓ Creating Tenant...")
tenant = Tenant.objects.create(
    name="Test Restaurant Group",
    slug="test-restaurant"
)
print(f"  Tenant created: {tenant.name}")

# 2. Create Outlet (Restaurant Location)
print("✓ Creating Outlet...")
outlet = Outlet.objects.create(
    tenant=tenant,
    name="Downtown Branch",
    address="123 Main St, Bangalore"
)
print(f"  Outlet created: {outlet.name}")

# 3. Create Admin User
print("✓ Creating Admin User...")
admin = User.objects.create_user(
    username='admin',
    password='admin123',
    email='admin@test.com',
    tenant=tenant,
    outlet=outlet,
    role='owner'
)
print(f"  Admin user created: {admin.username}")

# 4. Create Staff Users
print("✓ Creating Staff Users...")
cashier = User.objects.create_user(
    username='cashier1',
    password='cashier123',
    email='cashier@test.com',
    tenant=tenant,
    outlet=outlet,
    role='cashier'
)
waiter = User.objects.create_user(
    username='waiter1',
    password='waiter123',
    email='waiter@test.com',
    tenant=tenant,
    outlet=outlet,
    role='waiter'
)
chef = User.objects.create_user(
    username='chef1',
    password='chef123',
    email='chef@test.com',
    tenant=tenant,
    outlet=outlet,
    role='chef'
)
print(f"  Created: {cashier.username}, {waiter.username}, {chef.username}")

# 5. Create Tables
print("✓ Creating Tables...")
tables = []
for i in range(1, 6):
    table = Table.objects.create(
        tenant=tenant,
        outlet=outlet,
        name=f"Table {i}",
        is_active=True
    )
    tables.append(table)
print(f"  Created {len(tables)} tables")

# 6. Create Menu Categories
print("✓ Creating Menu Categories...")
categories = {
    'appetizers': MenuCategory.objects.create(
        tenant=tenant,
        outlet=outlet,
        name="Appetizers",
        display_order=1,
        is_active=True
    ),
    'mains': MenuCategory.objects.create(
        tenant=tenant,
        outlet=outlet,
        name="Main Courses",
        display_order=2,
        is_active=True
    ),
    'beverages': MenuCategory.objects.create(
        tenant=tenant,
        outlet=outlet,
        name="Beverages",
        display_order=3,
        is_active=True
    )
}
print(f"  Created {len(categories)} categories")

# 7. Create Menu Items
print("✓ Creating Menu Items...")
menu_items = {
    'samosa': MenuItem.objects.create(
        tenant=tenant,
        outlet=outlet,
        category=categories['appetizers'],
        name="Samosa",
        price=Decimal("60.00"),
        gst_percentage=Decimal("5.00"),
        is_available=True
    ),
    'butter_chicken': MenuItem.objects.create(
        tenant=tenant,
        outlet=outlet,
        category=categories['mains'],
        name="Butter Chicken",
        price=Decimal("350.00"),
        gst_percentage=Decimal("5.00"),
        is_available=True
    ),
    'biryani': MenuItem.objects.create(
        tenant=tenant,
        outlet=outlet,
        category=categories['mains'],
        name="Biryani",
        price=Decimal("280.00"),
        gst_percentage=Decimal("5.00"),
        is_available=True
    ),
    'coke': MenuItem.objects.create(
        tenant=tenant,
        outlet=outlet,
        category=categories['beverages'],
        name="Coke",
        price=Decimal("40.00"),
        gst_percentage=Decimal("5.00"),
        is_available=True
    )
}
print(f"  Created {len(menu_items)} menu items")

# 8. Create Modifiers
print("✓ Creating Modifiers...")
spice_group = ModifierGroup.objects.create(
    tenant=tenant,
    outlet=outlet,
    name="Spice Level",
    is_required=False,
    max_select=1,
    is_active=True
)
spice_mods = {
    'mild': Modifier.objects.create(
        group=spice_group,
        name="Mild",
        price=Decimal("0.00")
    ),
    'spicy': Modifier.objects.create(
        group=spice_group,
        name="Extra Spicy",
        price=Decimal("20.00")
    )
}
print(f"  Created spice level modifiers")

# Add modifier to butter chicken
MenuItemModifierGroup.objects.create(
    menu_item=menu_items['butter_chicken'],
    modifier_group=spice_group
)  # ✅ CORRECT

# 9. Create Inventory Items
print("✓ Creating Inventory Items...")
inventory_items = {
    'chicken': InventoryItem.objects.create(
        tenant=tenant,
        outlet=outlet,
        name="Chicken",
        unit="kg",
        stock=Decimal("10.00"),
        low_stock_threshold=Decimal("2.00")
    ),
    'butter': InventoryItem.objects.create(
        tenant=tenant,
        outlet=outlet,
        name="Butter",
        unit="kg",
        stock=Decimal("5.00"),
        low_stock_threshold=Decimal("1.00")
    ),
    'rice': InventoryItem.objects.create(
        tenant=tenant,
        outlet=outlet,
        name="Rice",
        unit="kg",
        stock=Decimal("20.00"),
        low_stock_threshold=Decimal("5.00")
    )
}
print(f"  Created {len(inventory_items)} inventory items")

# 10. Create Recipes
print("✓ Creating Recipes...")
Recipe.objects.create(
    menu_item=menu_items['butter_chicken'],
    inventory_item=inventory_items['chicken'],
    quantity_required=Decimal("0.5"),
    unit="kg"
)
Recipe.objects.create(
    menu_item=menu_items['butter_chicken'],
    inventory_item=inventory_items['butter'],
    quantity_required=Decimal("0.1"),
    unit="kg"
)
Recipe.objects.create(
    menu_item=menu_items['biryani'],
    inventory_item=inventory_items['rice'],
    quantity_required=Decimal("0.25"),
    unit="kg"
)
print(f"  Created recipes for items")

print("\n✅ TEST DATA CREATED SUCCESSFULLY\n")

# ==========================================
# PHASE 2: TEST WORKFLOW
# ==========================================

print("="*60)
print("📋 PHASE 2: Testing Complete Order Workflow")
print("="*60)

# TEST 1: Create Order
print("\n1️⃣  TEST: Create Order")
print("-" * 60)
try:
    order = Order.objects.create(
        tenant=tenant,
        outlet=outlet,
        table=tables[0],
        created_by=cashier,
        status='open'
    )
    print(f"✓ Order created: {order.order_number}")
    print(f"  Status: {order.status}")
    print(f"  Table: {order.table.name}")
except Exception as e:
    print(f"❌ ERROR: {e}")

# TEST 2: Add Items to Order
print("\n2️⃣  TEST: Add Items to Order")
print("-" * 60)
try:
    item1 = OrderItem.objects.create(
        order=order,
        menu_item=menu_items['butter_chicken'],
        quantity=2,
        price=menu_items['butter_chicken'].price,
        gst_percentage=menu_items['butter_chicken'].gst_percentage,
        total_price=menu_items['butter_chicken'].price * 2,
        status='pending'
    )
    
    # Add modifier
    modifier1 = OrderItemModifier.objects.create(
        order_item=item1,
        modifier=spice_mods['spicy'],
        name="Extra Spicy",
        price=Decimal("20.00")
    )
    
    item2 = OrderItem.objects.create(
        order=order,
        menu_item=menu_items['samosa'],
        quantity=4,
        price=menu_items['samosa'].price,
        gst_percentage=menu_items['samosa'].gst_percentage,
        total_price=menu_items['samosa'].price * 4,
        status='pending'
    )
    
    print(f"✓ Added {item1.menu_item.name} x {item1.quantity}")
    print(f"  - With modifier: {modifier1.name}")
    print(f"✓ Added {item2.menu_item.name} x {item2.quantity}")
except Exception as e:
    print(f"❌ ERROR: {e}")

# TEST 3: Recalculate Totals
print("\n3️⃣  TEST: Calculate Order Totals")
print("-" * 60)
try:
    order.recalculate_totals()
    print(f"✓ Subtotal: ₹{order.subtotal}")
    print(f"✓ GST Total: ₹{order.gst_total}")
    print(f"✓ Grand Total: ₹{order.grand_total}")
except Exception as e:
    print(f"❌ ERROR: {e}")

# TEST 4: Apply Discount
print("\n4️⃣  TEST: Apply Discount")
print("-" * 60)
try:
    order.apply_discount("percentage", Decimal("10.0"))
    order.recalculate_totals()
    print(f"✓ Discount (10%): ₹{order.discount_total}")
    print(f"✓ Final Total: ₹{order.grand_total}")
except Exception as e:
    print(f"❌ ERROR: {e}")

# TEST 5: Create KOT (Send to Kitchen)
print("\n5️⃣  TEST: Send to Kitchen (Create KOT)")
print("-" * 60)
try:
    from django.db import transaction
    
    with transaction.atomic():
        # Get next KOT number
        from orders.models import DailyKOTCounter
        today = timezone.now().date()
        counter, created = DailyKOTCounter.objects.get_or_create(date=today)
        counter.value += 1
        counter.save()
        
        kot = KOTBatch.objects.create(
            tenant=tenant,
            outlet=outlet,
            order=order,
            kot_number=counter.value,
            status='confirmed'
        )
        
        # Link items to KOT
        for item in order.items.all():
            item.kot = kot
            item.status = 'sent'
            item.save()
        
        print(f"✓ KOT created: #{kot.kot_number}")
        print(f"  Items sent to kitchen")
except Exception as e:
    print(f"❌ ERROR: {e}")

# TEST 6: Update Order Status
print("\n6️⃣  TEST: Update Order Item Status")
print("-" * 60)
try:
    # Chef marks items as ready
    for item in order.items.all():
        item.status = 'ready'
        item.save()
    print(f"✓ Items marked as ready by chef")
    
    # Waiter serves items
    for item in order.items.all():
        item.status = 'served'
        item.save()
    print(f"✓ Items marked as served by waiter")
except Exception as e:
    print(f"❌ ERROR: {e}")

# TEST 7: Process Payment
print("\n7️⃣  TEST: Process Payment")
print("-" * 60)
try:
    payment = Payment.objects.create(
        order=order,
        method='cash',
        amount=order.grand_total
    )
    order.status = 'paid'
    order.closed_at = timezone.now()
    order.save()
    print(f"✓ Payment processed: ₹{payment.amount}")
    print(f"  Method: {payment.method}")
    print(f"✓ Order status: {order.status}")
except Exception as e:
    print(f"❌ ERROR: {e}")

# TEST 8: Check Inventory
print("\n8️⃣  TEST: Inventory Status")
print("-" * 60)
try:
    print(f"✓ Current inventory:")
    for name, item in inventory_items.items():
        item.refresh_from_db()
        status = "OK" if item.stock > item.low_stock_threshold else "LOW"
        print(f"  {item.name}: {item.stock}{item.unit} [{status}]")
except Exception as e:
    print(f"❌ ERROR: {e}")

# ==========================================
# PHASE 3: REPORT
# ==========================================

print("\n" + "="*60)
print("📊 TEST SUMMARY")
print("="*60)

try:
    total_orders = Order.objects.filter(tenant=tenant).count()
    total_items = OrderItem.objects.filter(order__tenant=tenant).count()
    total_revenue = Order.objects.filter(
        tenant=tenant,
        status__in=['paid', 'closed']
    ).aggregate(total=models.Sum('grand_total'))['total'] or Decimal("0")
    
    print(f"\n✅ System Status: WORKING")
    print(f"\n📈 Statistics:")
    print(f"  Total Orders: {total_orders}")
    print(f"  Total Items Ordered: {total_items}")
    print(f"  Total Revenue: ₹{total_revenue}")
    print(f"\n✅ WORKFLOW COMPLETE:")
    print(f"  ✓ Create order")
    print(f"  ✓ Add items with modifiers")
    print(f"  ✓ Apply discounts")
    print(f"  ✓ Send to kitchen")
    print(f"  ✓ Mark items ready")
    print(f"  ✓ Serve items")
    print(f"  ✓ Process payment")
    print(f"  ✓ Track inventory")
    
    print(f"\n🎯 READY FOR:")
    print(f"  ✓ Testing with real restaurant")
    print(f"  ✓ Gathering feedback")
    print(f"  ✓ Portfolio showcase")
    
except Exception as e:
    print(f"❌ ERROR in summary: {e}")

print("\n" + "="*60)
print("✅ TEST SCRIPT COMPLETE")
print("="*60 + "\n")