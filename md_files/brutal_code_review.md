# 🔥 Brutal Code Review — Restaurant POS System
**Date:** April 24, 2026 | **Reviewer:** Antigravity | **Scope:** Full codebase

> I'm not going to be nice about this. The bones are solid but there are fundamental architectural problems, security holes, and enough dead code to fill a graveyard. Let's go.

---

## 🔴 CRITICAL — Will cause data loss, security breaches, or production crashes

---

### 1. `tenant_required` checks the wrong thing — the middleware is decorative

**Files:** `core/decorators.py`, `core/middleware.py`, all views

This is the biggest architectural issue in the entire codebase.

`TenantMiddleware` sets `request.tenant` from the subdomain.  
`tenant_required` checks `request.user.tenant` (a FK on the User model).

These are **two completely separate, independent values**. A user from `pizza-palace` can hit `spice-garden.domain.com`, `request.user.tenant` will be `pizza-palace`, and every single data filter in every view (`Order.objects.filter(tenant=request.user.tenant, ...)`) will happily return pizza-palace data from a spice-garden URL.

**`request.tenant` set by the middleware is never used for security.** It's set and then completely ignored. The middleware currently does nothing for data isolation — it's theatre.

```python
# EVERY view does this:
Order.objects.filter(tenant=request.user.tenant, ...)
# request.tenant is never validated against request.user.tenant anywhere
```

**Fix:** Either drop the middleware approach and accept user.tenant as the sole source of truth (simpler, correct), OR add a check in `tenant_required` that `request.tenant == request.user.tenant` and 403 if they differ.

---

### 2. `void_service.py` — No tenant/outlet guard on item fetch

**File:** `orders/services/void_service.py` — line 16

```python
item = (
    OrderItem.objects
    .select_for_update()
    .select_related("order")
    .get(id=item_id)   # ← NO tenant filter
)
```

Any authenticated user who knows an `item_id` from another tenant can void it by calling the void endpoint with that ID. There's no `.filter(order__tenant=user.tenant)` guard. This is a horizontal privilege escalation vulnerability.

**Fix:**
```python
.get(id=item_id, order__tenant=user.tenant, order__outlet=user.outlet)
```

---

### 3. `sales_dashboard` — One-click tenant deletion, no confirmation, no audit

**File:** `accounts/views.py` — lines 131-137

```python
elif action == "delete_client":
    tenant = Tenant.objects.filter(id=tenant_id).first()
    if tenant:
        tenant.delete()   # cascades to Orders, Payments, Refunds, everything
        messages.success(request, f"Client {name} deleted.")
```

A POST request with `action=delete_client&tenant_id=3` from a superuser account **permanently destroys an entire restaurant's data** — all orders, payments, refunds, menus, inventory. No confirmation. No soft-delete. No `OrderEvent` log. No backup check. If this fires accidentally, that data is gone.

**Fix:** Soft-delete (`is_active=False`), not hard-delete. Gate behind a separate confirmation endpoint. Log to `OrderEvent` or a dedicated audit table.

---

### 4. `kot_service.py` — Network I/O inside a DB transaction

**File:** `orders/services/kot_service.py` — lines 115-121

```python
@transaction.atomic   # ← DB lock held for entire function
def create_kot(user, order):
    ...
    if station and station.printer_ip:
        printer = PrintingService(...)
        printer.print_kot(order, kot)   # ← TCP socket to thermal printer
```

A TCP connection to a thermal printer is made **while the DB transaction is open and holding row locks** on `OrderItem`, `DailyKOTCounter`, and `KOTBatch`. If the printer is offline, slow, or times out (default socket timeout can be 30+ seconds), every other request trying to create a KOT for any table is blocked for that entire duration.

**Fix:** Return from the atomic block first, then print. Use a post-transaction hook or Django signals, or at minimum move printing outside the `@transaction.atomic` scope entirely.

---

### 5. `kitchen_service.py` — `set_item_preparing()` has no lock or transaction

**File:** `orders/services/kitchen_service.py` — lines 55-70

```python
def set_item_preparing(user, item_id):
    item = OrderItem.objects.get(...)   # no select_for_update
    if item.status != "sent":
        raise ValueError(...)
    item.status = "preparing"
    item.save(update_fields=["status"])
```

No `@transaction.atomic`. No `select_for_update`. Two kitchen staff hitting "Start" on the same item simultaneously will both pass the `status != "sent"` check and both try to save `"preparing"`. Compare to `set_item_ready()` right below it which correctly uses both. **Inconsistency in the same file.**

**Fix:** Add `@transaction.atomic` and `select_for_update()` identical to `set_item_ready()`.

---

### 6. `set_item_served()` — Same problem, no lock, no transaction

**File:** `orders/services/kitchen_service.py` — lines 108-125

```python
def set_item_served(user, item_id):
    item = OrderItem.objects.get(...)   # no lock, no atomic
    if item.status != "ready":
        raise ValueError(...)
    item.status = "served"
    item.save(...)
    update_table_state(item.order)   # second write, not atomic with first
```

Same race condition. The table state update and item status update are not in one transaction.

---

### 7. `inventory_service.py` — Partial deduction committed on multi-ingredient failure

**File:** `orders/services/inventory_service.py` — lines 52-87

```python
for recipe in recipes:
    with transaction.atomic():   # ← NEW transaction PER INGREDIENT
        inventory = InventoryItem.objects.select_for_update().get(...)
        inventory.stock -= required_quantity
        inventory.save()
```

If a dish has 3 ingredients and ingredient #2 fails (e.g., `ObjectDoesNotExist`), ingredient #1's deduction is **permanently committed**. The outer exception is caught and logged, meaning KOT creation continues with corrupted inventory. This silently under-counts stock.

**Fix:** Move the `transaction.atomic()` to wrap the entire `for recipe in recipes` loop, not each individual iteration.

---

### 8. `payment_service.py` — Refund payments break `paid_total` calculation

**File:** `orders/services/payment_service.py` — line 31

```python
paid_total = order.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
remaining = order.grand_total - paid_total
```

After our fix, approved refunds create negative `Payment` records. This `Sum("amount")` now includes those negatives. If an order was paid ₹500 and ₹100 was refunded, `paid_total = 400`, `remaining = 100`. A customer could now pay ₹100 again on an already-closed order if it's re-opened. The guard `if remaining <= 0: raise ValidationError("Order already fully paid")` would incorrectly pass.

**Fix:** Exclude `method="refund"` from `paid_total` calculation in `process_payment()`.

```python
paid_total = order.payments.exclude(method="refund").aggregate(...)["total"] or Decimal("0")
```

---

## 🟠 HIGH — Logic bugs and security gaps that will bite you

---

### 9. `accounts/views.py` — Two rate limiters fighting each other

**File:** `accounts/views.py` — lines 21-27

You have manual IP-based cache rate limiting AND `django-axes` both running on login. They're doing the same job with different data stores. The manual one tracks by IP only (5 attempts, 5-minute window). Axes tracks by username+IP (configurable). They're independent — a user locked by one is not locked by the other. This creates confusion about which system is actually in effect and can lead to under-protection (if one resets and the other doesn't).

**Verdict:** Remove the manual cache rate limiter entirely. You already have Axes. Pick one.

---

### 10. `accounts/views.py` — `KeyError` on missing POST fields

**File:** `accounts/views.py` — lines 31-32

```python
username = request.POST["username"]   # ← KeyError if field missing
password = request.POST["password"]   # ← KeyError if field missing
```

Use `.get()`. A malformed POST (e.g., from a bot or API fuzzer) will throw an unhandled `KeyError` that returns a 500 instead of a 400.

---

### 11. `accounts/views.py` — Triple duplicate imports

**File:** `accounts/views.py` — lines 2-10

```python
from django.shortcuts import render, redirect   # line 2
from django.shortcuts import render             # line 6
from django.shortcuts import redirect           # line 10
```

Three separate imports of the same symbols from the same module. This is not a one-time mistake — it's been copy-pasted at least twice. Indicates no one is running a linter.

---

### 12. `kitchen_service.py` — Billing orders hidden from kitchen display

**File:** `orders/services/kitchen_service.py` — line 14

```python
kots = KOTBatch.objects.filter(
    order__status="open"   # ← "billing" orders excluded
)
```

When a customer requests the bill (`order.status = "billing"`), the kitchen display immediately stops showing their KOTs — even if food is still being prepared. A chef finishing a dish for a billing order won't see it on the display.

**Fix:** Filter on `order__status__in=["open", "billing"]`.

---

### 13. `table_views.py` — `tables_data()` breaks prefetch with filtered querysets

**File:** `orders/views/table_views.py` — lines 87-98

```python
orders = Order.objects.filter(...).prefetch_related("items")
orders_map = {o.table_id: o for o in orders}
...
# In the loop:
items = order.items.all()         # ← uses prefetch cache ✓
items.filter(status="pending")    # ← NEW queryset, BREAKS prefetch ✗
items.filter(status__in=[...])    # ← another new queryset ✗
```

Every `items.filter(...)` call inside the loop creates a fresh SQL query, completely bypassing `prefetch_related`. For a floor with 20 tables, this is potentially **100+ extra queries per poll cycle** (tables_data is called repeatedly for the live dashboard).

**Fix:** Fetch all items in the prefetch and filter in Python:
```python
all_items = list(order.items.all())  # uses prefetch
pending = [i for i in all_items if i.status == "pending"]
```

---

### 14. `manage_table_view` — No role guard, no input validation

**File:** `orders/views/table_views.py` — lines 270-274

```python
if action == "create":
    name = data.get("name")   # can be None
    Table.objects.create(name=name, ...)   # IntegrityError if None
```

- Any `@tenant_required` user (including waiters, chefs) can create tables.
- `name=None` will cause an `IntegrityError` swallowed by the outer `except Exception`.
- No `@role_required("manager", "owner")` guard.

---

### 15. `kot_service.py` — Pointless `refresh_from_db()` inside locked atomic block

**File:** `orders/services/kot_service.py` — line 81

```python
counter, _ = DailyKOTCounter.objects.select_for_update().get_or_create(...)
# In loop:
counter.value += 1
counter.save(update_fields=["value"])
counter.refresh_from_db()   # ← completely pointless
kot_number = counter.value
```

The row is locked with `select_for_update`. No other transaction can write to it. `save()` just wrote the new value. `refresh_from_db()` reads back the exact value that was just written — a wasted round-trip query for every station group in the loop.

---

### 16. `validate_order_payment()` — ₹0.02 tolerance is dangerous

**File:** `orders/utils/payment_utils.py` — line 16

```python
TOLERANCE = Decimal("0.02")
if abs(paid - order.grand_total) > TOLERANCE:
    raise ValidationError(...)
```

A ₹0.01 or ₹0.02 discrepancy silently passes financial validation. On an order of ₹500, this is fine. But this function is called `validate_order_payment` — it's supposed to be a hard guarantee. If `recalculate_totals()` and `split_bill()` both round correctly with `ROUND_HALF_UP`, the tolerance should be `Decimal("0.00")` or at most `Decimal("0.01")`. A ₹0.02 tolerance means financial reports could be off by up to 2 paise per order — small per order, but multiplied across thousands of orders it accumulates.

---

## 🟡 MEDIUM — Technical debt and missing features that hurt quality

---

### 17. Dead files polluting the repository root

```
old_billing.py          35 KB   ← dead code
fix_urls.py              3 KB   ← one-off script
tmp_update_css.py        5 KB   ← one-off script  
update_fonts.py          1 KB   ← one-off script
test.py                 10 KB   ← not in tests/
test_critical.py         5 KB   ← not in tests/
test_full_workflow.py   12 KB   ← not in tests/
test_pay.py              2 KB   ← not in tests/
pos.zip                3.9 MB   ← committed to git
```

The project root is a junkyard. `pos.zip` alone adds 3.9 MB to every `git clone`. The test files aren't in a `tests/` directory so Django's test runner won't discover them automatically. These appear to be manual debug scripts, not unit tests.

**Action:** Delete everything above. Move real tests to `orders/tests/`, `accounts/tests/`, etc.

---

### 18. `.env` file committed to git

**File:** `f:\pos\.env` (366 bytes, exists in the repo)

Your `.env` file is in the repository root and presumably tracked by git. That means `SECRET_KEY`, `DB_PASSWORD`, `SENTRY_DSN`, AWS credentials — everything — is in your git history. Even if you delete it now, it's in every previous commit.

**Action:** `git rm --cached .env`, add to `.gitignore`, rotate every secret in the file immediately.

---

### 19. `requirements.txt` — Unpinned Django version

**File:** `requirements.txt` — line 1

```
Django>=5.0   # ← anything from 5.0 to infinity
```

The settings file docstring says `Django 6.0.3`. The requirement allows any version ≥5.0. A `pip install` in a fresh environment could install 5.0, 5.1, 5.2, or 6.x depending on what's available. **Pin your dependencies:**

```
Django==6.0.3
```

---

### 20. `check_inventory_availability()` — Never called, completely dead

**File:** `orders/services/inventory_service.py` — lines 101-132

The function `check_inventory_availability()` is defined, has a docstring mentioning future uses like "blocking out-of-stock items", and is never imported or called anywhere. Items can be ordered even when stock is 0. The function exists as a promise that was never kept.

---

### 21. `sales_reports.py` — Unused imports introduced in recent fix

**File:** `reports/services/sales_reports.py`

```python
from django.db.models import Sum as _Sum   # Sum already imported at top
from django.db.models import Q as _Q       # Q never used here
```

These were added inside the function body during the refund fix. Both are dead code.

---

### 22. `accounts/views.py` — `owner_dashboard` not using `@tenant_required`

**File:** `accounts/views.py` — line 72

```python
@login_required
def owner_dashboard(request):
    if request.user.role not in ["owner","manager"]:
        return HttpResponseForbidden()
    metrics = owner_dashboard_metrics(request.user)
```

`owner_dashboard_metrics(request.user)` presumably calls `request.user.tenant` and `request.user.outlet`. If a superuser account has no tenant/outlet assigned, this crashes. No `@tenant_required` guard.

---

### 23. No tests that a CI pipeline can run

The test files in the project root are not discoverable by `python manage.py test` or `pytest`. There are no `TestCase` classes, no fixtures, no factories. The "tests" are procedural scripts. There is zero automated test coverage protecting any of the financial logic — `recalculate_totals`, `process_payment`, `approve_refund`, `split_bill`.

**This is the biggest long-term risk.** Every bug fix you make could break something else with no safety net.

---

## 🟢 MINOR — Polish and hygiene

| Issue | File | Detail |
|-------|------|--------|
| `import json` inside view functions | `table_views.py` lines 166, 194, 261 | Move to top of file |
| Inline imports inside `pay_order` | `billing_views.py` line 287 | `from shifts.models import CashSession` inside view |
| `gst_percentage=0` hardcoded in webhook | `orders/api.py` line 158 | Comment says "Simplified for example" — it's been like this for months |
| No `__str__` on `TableMerge` | `orders/models.py` | Makes admin and logging unreadable |
| `logger` defined after imports are used | `orders/api.py` line 87 | `logger` defined after `from` imports at line 80+ |
| `DailyKOTCounter` fields are `null=True` | `orders/models.py` line 692-693 | `tenant` and `outlet` are nullable on a counter that requires them |
| Bare `except:` equivalent via `except Exception` swallows all errors | Multiple views | Especially `table_views.py` per-table error swallowing |

---

## 📅 When to Implement Subdomain Routing

This is also a question about **when this project is production-ready**. Here's the honest timeline:

### Phase 1 — Fix before any real user touches this (Now)
- [ ] Fix `void_service.py` tenant guard (30 min)
- [ ] Fix `set_item_preparing/served` locking (1 hour)
- [ ] Fix inventory atomic scope (30 min)
- [ ] Fix `payment_service.py` refund exclusion (15 min)
- [ ] Remove `.env` from git, rotate all secrets (1 hour)
- [ ] Pin `Django==6.0.3` in requirements (5 min)
- [ ] Remove manual cache rate limiter from login (15 min)
- [ ] Fix `kitchen_service` billing order filter (5 min)
- [ ] Fix `manage_table_view` role guard (15 min)

### Phase 2 — Before beta (1-2 weeks)
- [ ] Move KOT printing out of the transaction
- [ ] Fix `tables_data()` N+1 prefetch break
- [ ] Write real Django `TestCase` tests for financial logic (3-5 days)
- [ ] Clean up repository root (delete dead files, zip, scripts)
- [ ] Fix `sales_dashboard` to use soft-delete

### Phase 3 — When you have a real domain (1-3 months)
- [ ] Decide: `request.user.tenant` vs `request.tenant` — pick ONE
- [ ] If going subdomain-first: enforce `request.tenant == request.user.tenant` in `tenant_required`
- [ ] Get a domain, set wildcard DNS `*.yourdomain.com → server IP`
- [ ] Configure Nginx wildcard vhost with `proxy_set_header Host $host`
- [ ] Update `ALLOWED_HOSTS = [".yourdomain.com"]`
- [ ] Drop the `?tenant=slug` dev fallback from middleware (or guard it behind `DEBUG=True`)

### Phase 4 — Production hardening (Before paying customers)
- [ ] Celery for async KOT printing (replace daemon thread risk)
- [ ] Implement `check_inventory_availability()` at order time
- [ ] Real test suite with CI (GitHub Actions)
- [ ] Database connection pooling (`pgbouncer` or `CONN_MAX_AGE`)
- [ ] Structured logging (JSON format for log aggregation)
- [ ] Proper backup strategy before `tenant.delete()` is possible

---

## Verdict

The financial core — `recalculate_totals`, `process_payment`, `approve_refund`, KOT counter, locking — is genuinely solid. Someone spent real time getting that right and it shows.

But the surrounding code is inconsistent. Services use row locks while views do the same operation without them. One module has `@transaction.atomic` done properly, the next one doesn't. Dead files everywhere. The security architecture around multi-tenancy is conceptually broken (middleware vs user.tenant).

**The codebase is not ready for production.** It's alpha-quality with production-quality pockets. Fix the critical items above before any real restaurant data goes into this system.

---
*Review complete. No feelings were spared.*
