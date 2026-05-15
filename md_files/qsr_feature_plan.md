# QSR Feature Plan — Revised After Codebase Audit
**Date: May 10, 2026**

---

## What Already Exists (Do NOT Rebuild)

| Feature | Where It Lives | Status |
|---------|---------------|--------|
| Recipe model (`menu_item → inventory_item, quantity`) | `inventory/models.py:470` | ✅ Built |
| Inventory deduction (`InventoryItem.reduce_stock()`) | `inventory/models.py:115` | ✅ Built |
| Auto PO on low stock (`trigger_reorder()`) | `inventory/models.py:159` | ✅ Built |
| Supplier model + PurchaseOrder lifecycle | `inventory/models.py:274` | ✅ Built |
| Zomato/Swiggy webhook receiver | `orders/api.py:189` | ✅ Built |
| `AggregatorConfig` per outlet (enabled + secret) | `setup/models.py:88` | ✅ Built |
| `Order.source` field (counter/zomato/swiggy) | `orders/models.py:73` | ✅ Built |
| Per-item platform availability (`available_zomato`) | `menu/models.py:112` | ✅ Built |

---

## What Needs Building

### Feature A — Recipe Sync from Tenant → All Outlets

**Problem:** Recipes are entered per outlet. If you have 10 franchises and add a new item with a 3-ingredient recipe, you enter it 30 times.

**Solution:** One button — "Sync Recipes to All Outlets"

**How it works:**
```
Owner defines recipe at head-office outlet
→ POST /menu/sync-recipes-to-outlets/
→ For each item in source outlet:
    For each recipe:
        Find matching item by NAME in each target outlet
        get_or_create(menu_item=match, inventory_item=matching_inv, quantity=recipe.quantity)
```

**Rules:**
- Inventory items are matched by NAME — both outlets must have identically named items
- If no matching inventory item exists at target outlet: skip + log warning
- Owner-only action

---

### Feature B — Inventory Deduction When Order is Paid

**Problem:** `reduce_stock()` exists but is never called when an order closes.

**Where to hook it:** `payment_service.py` — after `order_closed = True`:

```python
# Deduct inventory for each sold item
for order_item in order.items.filter(status="served"):
    for recipe in order_item.menu_item.recipes.all():
        try:
            recipe.inventory_item.reduce_stock(
                recipe.quantity_required * order_item.quantity,
                reference=f"Order #{order.id}"
            )
        except ValidationError:
            logger.warning("Insufficient stock for %s", recipe.inventory_item.name)
            # Don't block payment — just log
```

**This automatically triggers:**
- `InventoryTransaction` record (audit trail)
- Low stock notification
- Auto Draft PO to supplier if stock drops below threshold

---

### Feature C — Online Order Toggle (Token Dashboard + Owner Dashboard)

**The Toggle Controls:**
1. **Master Online Toggle** — turn all aggregator orders on/off for an outlet instantly
2. **Per-Platform Toggle** — Zomato ON, Swiggy OFF independently

**Where to show it:**
- Token Dashboard header (quick toggle — cashier can pause online orders during rush)
- Owner Dashboard (management view with per-branch visibility)

**Backend:** `AggregatorConfig` model already has `zomato_enabled` and `swiggy_enabled`. The webhook receiver in `api.py` already checks this flag.

**New endpoint needed:**
```
POST /setup/toggle-aggregator/
body: { platform: "zomato" | "swiggy" | "all", enabled: true | false }
```

**UI — Token Dashboard toggle:**
```
┌──────────────────────────────────┐
│  Online Orders                   │
│  [Zomato ●] [Swiggy ○] [All ●] │
└──────────────────────────────────┘
```

---

### Feature D — QSR Menu Management (Clean UI)

**Problem:** Current `menu_management.html` is 110KB with Zomato/Swiggy toggles, AI importer, station assignment, modifier panels — irrelevant and confusing for a QSR outlet manager.

**Solution:** Detect `franchise`/`cafe` tenant type in the view → render `qsr_menu_management.html`

**QSR template contains only:**

| Element | Interaction |
|---------|-------------|
| Category list | Add / rename / delete |
| Item name | Inline edit on tap |
| Item price | Inline edit on tap |
| Available toggle | One tap — green/grey |
| Inventory stock level | Read-only badge (e.g. "Coffee Beans: 2kg") |
| Low stock warning badge | Auto-shown when stock ≤ threshold |

**No** recipes panel, no stations, no Zomato/Swiggy per-item toggles (those are platform-level, not per-item for QSR).

---

## Build Order

| # | Feature | Effort | Blocks |
|---|---------|--------|--------|
| B | Inventory deduction on payment | 30 min | Nothing |
| C | Online order toggle UI | 1.5h | Nothing |
| D | QSR Menu Management template | 2h | Nothing |
| A | Recipe sync to outlets | 1h | D (needs outlet context) |

> [!IMPORTANT]
> Reply **"build it"** to start in order B → C → D → A.

---

## Zomato / Swiggy Live Orders — What's Missing

The webhook receiver is built. What you need before it works:

| Requirement | Who Provides It | Status |
|-------------|-----------------|--------|
| Webhook URL registered on Zomato Partner Portal | You (after approval) | ❌ Not done |
| `zomato_webhook_secret` set in Outlet Settings | You | ❌ Not configured |
| Zomato Partner Program approval | Zomato | ❌ Pending |
| Swiggy Restaurant Partner Program approval | Swiggy | ❌ Pending |

**Action:** Go to [Zomato Partner](https://www.zomato.com/partner) and [Swiggy Restaurant Partner](https://partner.swiggy.com/) and register. Once approved, they give you an API key and webhook secret. Enter them in `/setup/aggregator/` and online orders will start flowing in automatically.
