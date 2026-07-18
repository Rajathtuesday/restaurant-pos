# Parcel Charge — How It Works (ELI5 + Micro Details)

---

## 1. Where the data lives

Two places on the server:

**`Outlet.parcel_charge_amount`** — the *unit price* per item (e.g. ₹5).  
Set once by the owner in settings. Never changes per order.

**`Order.parcel_surcharge`** — the *actual charge* on a specific order (e.g. ₹25 if 5 items × ₹5).  
Stored on the order row itself. Starts at `0`.

---

## 2. The server toggle view (`toggle_parcel`)

Simple flip-flop:

```
if order.parcel_surcharge > 0:
    set it to 0          ← parcel OFF
else:
    calculate and set    ← parcel ON
```

Calculate means: `item_qty × outlet.parcel_charge_amount` (respects `parcel_charge_per_item` setting).  
After setting, calls `recalculate_totals()` which rebuilds the grand total including that surcharge and saves everything.

**Critical rule**: call once → ON. Call again → OFF. It is a toggle, not a setter.

---

## 3. The bill template

`orders/templates/orders/bill.html` has this block:

```django
{% if order.parcel_surcharge %}
  <tr><td>Parcel Charge</td><td>₹{{ order.parcel_surcharge }}</td></tr>
{% endif %}
```

The bill reads directly from `Order.parcel_surcharge`.  
If that field is 0, the row is invisible. If it's 25, it shows ₹25. Nothing fancy — just the database value.

---

## 4. Client-side state

The browser tracks two things:

```javascript
let _parcelOn = false;             // does the USER want parcel?
const _parcelAmount = 5;           // unit price from outlet settings (read-only)
```

`_parcelOn` is purely the user's **intent**. It has no idea what the server actually has.

---

## 5. User presses the parcel button (`toggleParcel`)

**Case A — no order exists yet** (user hasn't dispatched anything):
```
_parcelOn flips (false → true or true → false)
UI updates (button turns gold, row appears)
Server is NOT called
```
The server doesn't know parcel is wanted. It will be told later.

**Case B — order already exists:**
```
Calls toggle-parcel on the server
Server flips the actual parcel_surcharge
Server returns { parcel_on: true/false, parcel_amount: 25, grand_total: 225 }
_parcelOn = d.parcel_on              ← follows server truth
_parcelAppliedToOrder = orderId      ← remembers which order has it applied
UI updates with real rupee amount
```

---

## 6. User presses Dispatch (`sendToKitchen`)

**Step by step:**

```
Step 1: POST /create-order
        → returns { order_id: 7 }
        (If table 3 already has an open order, returns the same order_id: 7)
        (If new table/takeaway, returns a fresh order_id: 8)

Step 2 (FIXED):
        needsParcel = _parcelOn
                      && _parcelAmount > 0
                      && _parcelAppliedToOrder !== d.order_id

        if (needsParcel):
            POST /toggle-parcel/7/   ← only fired when not already applied

Step 3: POST /send-to-kitchen/7/

Step 4: on full success:
        if (needsParcel) _parcelAppliedToOrder = d.order_id
```

---

## 7. The Bug (what was broken before the fix)

Timeline with dine-in — user dispatches twice:

| Event | `_parcelOn` | Server `parcel_surcharge` | Result |
|---|---|---|---|
| User taps "Add Parcel" (no order) | `true` | `0` | UI flip only |
| **Dispatch #1** | `true` | `0 → 25` | toggle called → **ON** ✓ |
| User adds 2 more items | `true` | `25` | still on |
| **Dispatch #2** | `true` | `25 → 0` | toggle called again → **OFF** ✗ |

`create-order` returns `order_id: 7` both times (same open table order).  
The toggle was called both times.  
Server sees 25 > 0 on the second call and resets it to 0.  
Bill shows no parcel charge.

---

## 8. The Fix

Added one tracking variable:

```javascript
let _parcelAppliedToOrder = null;  // order_id that parcel is currently applied to on the server
```

### How it's updated

| Where | What happens |
|---|---|
| `toggleParcel()` — server call succeeds | `_parcelAppliedToOrder = parcel_on ? orderId : null` |
| `sendToKitchen()` — full dispatch succeeds | `if (needsParcel) _parcelAppliedToOrder = d.order_id` |
| App loads / no order | stays `null` |

### Same timeline after the fix

| Event | `_parcelOn` | `_parcelAppliedToOrder` | Server `parcel_surcharge` |
|---|---|---|---|
| User taps "Add Parcel" (no order) | `true` | `null` | `0` |
| **Dispatch #1** → order_id=7, `null !== 7` → fires toggle | `true` | `7` | `0 → 25` ✓ |
| User adds 2 more items | `true` | `7` | `25` |
| **Dispatch #2** → order_id=7, `7 !== 7` is false → **skips** toggle | `true` | `7` | `25` (untouched) ✓ |
| User taps "Remove Parcel" | `false` | `null` | `25 → 0` |
| **Dispatch #3** → order_id=7, `null !== 7` → fires toggle | `true` | `7` | `0 → 25` ✓ |

`_parcelAppliedToOrder` acts as memory: *"I already told order 7 about parcel — don't tell it again."*  
If the user manually removes parcel, it resets to `null`, so the next dispatch re-applies it.  
If a brand new order is created (order_id=8), the old value `7` doesn't match, so parcel is applied to the new order correctly.

---

## 9. Files involved

| File | Role |
|---|---|
| `tenants/models.py` — `Outlet.parcel_charge_amount` | Unit price setting |
| `tenants/models.py` — `Outlet.parcel_charge_per_item` | Flat vs. per-item mode |
| `orders/models.py` — `Order.parcel_surcharge` | Actual charge stored on order |
| `orders/views/order_actions.py` — `toggle_parcel` | Server flip-flop view |
| `orders/templates/orders/bill.html` | Displays parcel row if > 0 |
| `orders/templates/orders/billing.html` | JS state + dispatch logic (the fix is here) |
