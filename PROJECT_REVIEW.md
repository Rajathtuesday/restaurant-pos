# 🔍 Project Review — Fine Dining POS
### Honest, Harsh, Actionable

---

## 📊 Overall Score: 6.2 / 10

> This is a **strong foundation with serious cracks.**
> The architecture shows real engineering thinking, but there are critical issues
> that would cause failures in a real restaurant on a busy Friday night.

---

## ✅ WHAT IS GOOD

### 1. Domain Model Quality — **9/10**
The `orders/models.py` is genuinely well designed.
- `OrderEvent` with `before_state`/`after_state` JSON is production-grade audit trail thinking.
- `OrderLock` with expiry timestamps is a proper solution to the billing concurrency problem.
- `Payment.on_delete=PROTECT` on the Order FK is the correct financial data protection pattern.
- `recalculate_totals()` with `Decimal` and `ROUND_HALF_UP` — no float math bugs. Good.
- `KOTBatch` → `OrderItem` relationship is modelled correctly.

### 2. Service-Oriented Architecture — **7/10**
`orders/services/` has 13 service files properly separating concerns:
`kitchen_service`, `payment_service`, `kot_service`, `refund_service`, etc.
This is the right pattern. Most junior developers dump everything into views.

### 3. Multi-tenancy — **7/10**
`tenant` + `outlet` on every model is the right approach for a SaaS POS.
The `TenantMiddleware` and `@tenant_required` decorator show awareness.

### 4. Database — **8/10**
PostgreSQL with `select_for_update()` on critical payment paths. Correct.
Compound indexes on `[tenant, outlet]` on every table. Correct.
`UniqueConstraint` with `condition=Q(status="open")` for one active order per table. Smart.

---

## 🔴 CRITICAL ISSUES — Would Break in Production

### ❌ ISSUE 1: `views.py` is 1,300 Lines — A God Object
**File:** `orders/views.py` — 1,300 lines, 38KB

This is the single biggest architectural problem.
`billing_view`, `payment`, `kitchen`, `table ops`, `merge`, `transfer`, `waiter calls`
— all crammed into one file. You started a service layer but didn't finish the extraction.

```
orders/views.py  → 1300 lines  ← UNACCEPTABLE
menu/views.py    → 470 lines   ← Getting better
```

**Impact**: Hard to test, maintain, or onboard a developer. A bug in payment
logic changes the same file as a table merge. That's a deployment risk.

---

### ❌ ISSUE 2: Payment Methods Stored in Session (Not Database)
**File:** `setup/views.py` Line 228

```python
# CURRENT — THIS IS WRONG
request.session["payment_methods"] = methods
```

Payment config stored in session means:
- It disappears when the session expires.
- Different cashiers on different browsers see different payment options.
- A server restart wipes it.
- Impossible to audit which payment methods were available on a given date.

**This needs a `PaymentConfig` model immediately.** The comment in the code even admits it:
> *"For MVP we store payment methods in session. Later this should be a PaymentConfig model"*

---

### ❌ ISSUE 3: `TableMerge.Meta` Has a Typo — Index Never Applied
**File:** `orders/models.py` Line 624

```python
class META:   ← WRONG (capital META is ignored by Django)
    indexes = [...]
```

Should be `class Meta:`. **This index silently does not exist.**
Table merge queries on a large dataset will do full table scans.

---

### ❌ ISSUE 4: `setup_staff` Bypasses Django's `create_user()`
**File:** `setup/views.py` Lines 273–285

```python
User.objects.create(
    username=username,
    password=make_password(password),  # ← Manual hashing
    ...
)
```

Should be `User.objects.create_user(username, password=password)`.
`create_user()` handles password hashing, `is_active`, and signal dispatching correctly.
Manual `make_password()` bypasses Django's password validation system.

---

### ❌ ISSUE 5: `except:` Bare Except in Table Setup
**File:** `setup/views.py` Line 69

```python
try:
    count = int(request.POST.get("table_count"))
except:                    ← NEVER use bare except
    messages.error(...)
```

This silently catches ALL exceptions including `KeyboardInterrupt`, `SystemExit`, memory errors.
Should be `except (ValueError, TypeError):`.

---

### ❌ ISSUE 6: `OrderEvent` Exists But Is Barely Used
**File:** `orders/models.py` Lines 462–547

You built a beautiful, production-grade event log model with `before_state`/`after_state`.
But in 1,300 lines of `views.py`, I can only find **one place** where `OrderEvent.objects.create()` is actually called — the table transfer.

Payments, KOT sends, discounts, and order creates are NOT writing `OrderEvent` records.
The entire audit log model is essentially decorative right now.

---

### ❌ ISSUE 7: `DEBUG=False` in `.env` But No Proper Error Pages
Running `DEBUG=False` in production is correct, but you have no custom 404/500 templates.
Customers and staff will see Django's ugly white error screen.

---

### ❌ ISSUE 8: No API Rate Limiting or CSRF on Some Endpoints
Several `@login_required` views accept POST JSON but have no protection against:
- Rapid repeated calls (no throttle)
- The `call_waiter` endpoint in `menu/views.py` has **no login required** — an unauthenticated person with a QR code can spam waiter calls infinitely.

---

## 🟡 MODERATE ISSUES — Should Fix Soon

### ⚠️ ISSUE 9: `pos.log` is 23KB and Growing with No Rotation
**File:** `core/settings.py` — Logging config uses `logging.FileHandler`.

`FileHandler` writes forever. On a busy restaurant doing 300 orders/day,
this file will hit 100MB in a month. Use `RotatingFileHandler` instead.

```python
# CORRECT
"class": "logging.handlers.RotatingFileHandler",
"maxBytes": 5 * 1024 * 1024,  # 5MB
"backupCount": 7,              # Keep 7 days
```

---

### ⚠️ ISSUE 10: `requirements.txt` is Not a Real Requirements File
```
asgiref==3.11.1
Django==6.0.3
...
```

It has graphviz and pydotplus (ER diagram tools) pinned as production dependencies.
These are dev/debug tools that have no place in a production deployment.
You need `requirements.txt` (prod) and `requirements-dev.txt` (dev tools).

---

### ⚠️ ISSUE 11: No `__str__` Fallback for Deleted Menu Items
**File:** `orders/models.py` Line 346

```python
def __str__(self):
    return f"{self.menu_item.name} x {self.quantity}"
    # If menu_item is deleted → AttributeError crash
```

`OrderItem.menu_item` is `on_delete=CASCADE`. If someone deletes a menu item,
historic orders that reference it will fail to render.
Should be `on_delete=SET_NULL` with a `name` snapshot field.

---

### ⚠️ ISSUE 12: `split_service.py` is Empty (228 bytes)
A bill split service exists but has no implementation.
If a customer asks to split a bill, there is no mechanism to do so.

---

### ⚠️ ISSUE 13: Setup Templates are Old / Unmodernized
The new premium Fine Dining UI was applied to billing, kitchen, inventory, waiter dashboard.
But `setup/` templates were never touched. A manager doing initial setup sees a completely
different, outdated interface — breaking the premium brand experience from minute one.

---

## 🟢 MINOR ISSUES

| # | Issue | File |
|---|---|---|
| 14 | Duplicate imports: `render`, `login_required` imported twice at top | `orders/views.py` L1–60 |
| 15 | `from heapq import merge` imported but never used | `orders/views.py` L3 |
| 16 | `import inventory` (unused module import) | `menu/views.py` L5 |
| 17 | `test.py` and `test_full_workflow.py` at root level, not in a `tests/` folder | root |
| 18 | `PROJECT_HALTED.md` exists in root — suggests the project was stopped before | root |
| 19 | No `README.md` — a developer joining this project has no onboarding guide | root |
| 20 | `pos.zip` (2.6MB) committed to the repo — build artifacts should be gitignored | root |

---

## 📋 Priority Fix List

| Priority | Issue | Effort |
|---|---|---|
| 🔴 **P0** | Fix `TableMerge.Meta` typo (silent bug right now) | 30 seconds |
| 🔴 **P0** | Migrate payment methods from session → `PaymentConfig` model | 2 hours |
| 🔴 **P0** | Replace `FileHandler` with `RotatingFileHandler` | 5 minutes |
| 🔴 **P1** | Fix `call_waiter` — add rate limiting / abuse protection | 30 minutes |
| 🔴 **P1** | Replace `except:` with typed exceptions in `setup/views.py` | 10 minutes |
| 🟡 **P2** | Use `create_user()` instead of manual `make_password` | 10 minutes |
| 🟡 **P2** | Wire `OrderEvent.objects.create()` into payment + KOT flows | 1 hour |
| 🟡 **P2** | Split `orders/views.py` into view controllers by feature | 3 hours |
| 🟡 **P3** | Modernize Setup templates to match Fine Dining design | 2 hours |
| 🟢 **P4** | Remove unused imports (`heapq.merge`, `inventory`) | 5 minutes |
| 🟢 **P4** | Add `requirements-dev.txt` for debug tools | 10 minutes |

---

## 🏁 Verdict

> This POS would **not survive a real dinner service** as-is.
> The `TableMerge.Meta` typo and session-based payment config are production-breaking.
> The `OrderEvent` model is one of the smartest things in this codebase
> — and it's sitting there wasted. Wire it up.
>
> Fix the P0s first. The bones of this project are genuinely solid.
> It just needs to be finished properly.

---

*Reviewed: 2026-03-30 | Fine Dining POS*
