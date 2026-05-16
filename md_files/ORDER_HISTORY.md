# Order History
## What it is, who sees what, every edge case, how to use it.

---

## The One-Line Summary

Every order ever placed — searchable, filterable, clickable — with full item breakdown, payment records, and an audit trail of who did what and when.

---

## Where to Find It

```
URL:          /history/
Nav:          Clock icon (🕐) in the top-right header on every page
Export:       /history/export/  (owner and manager only)
Order detail: /history/<id>/detail/  (slide-in panel, no page reload)
```

---

## What Each Role Sees

```
OWNER / MANAGER
  ✓ All orders for their outlet
  ✓ Any date range — today, last year, custom
  ✓ Full audit trail (who voided what, who applied discount, when)
  ✓ Staff filter (show only orders by Ravi, or only by Priya)
  ✓ Export to CSV
  ✓ Reprint any receipt

CASHIER
  ✓ All outlet orders (not just their own)
  ✓ Last 30 days only (date range locked)
  ✗ No audit trail
  ✗ No CSV export
  ✗ Cannot go beyond 30 days even by URL manipulation

WAITER
  ✓ Only orders THEY created
  ✓ Today only (date locked)
  ✗ No audit trail, no export
  ✗ Cannot see other waiters' orders

KITCHEN / CHEF
  ✗ No access — 403 Forbidden
```

---

## The Filter Bar

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ From [date] │ To [date] │ Status ▾ │ Payment ▾ │ Source ▾ │ Staff ▾ │ 🔍    │
│                                                                   [Search][Clear] │
└──────────────────────────────────────────────────────────────────────────────┘

Restricted roles (cashier / waiter) see a 🔒 icon and a note explaining
their date lock. They cannot change the date range even if they try the URL.
```

**Status options:**
- All (excluding open — open orders are on the billing screen)
- All including open (shows live orders too)
- Closed / Paid
- Cancelled
- Billing (generated but not yet paid)

**Payment options:**
- All
- Cash
- UPI
- Card
- Complimentary (grand total = ₹0)

**Source options:**
- All
- Dine-in, Takeaway, Zomato, Swiggy, QR Menu

**Free-text search:**
Searches: order number (INV-1-...) OR customer phone OR customer name.

---

## The Summary Strip

Appears above the table, updates with every filter change:

```
┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────────────────────┐
│ 47           │  │ Rs.18,420        │  │ Rs.391 avg       │  │ ● Cash Rs.8,200  ● UPI Rs.9,100 │
│ Orders       │  │ Revenue          │  │                  │  │ ● Card Rs.1,120   Payment split  │
└──────────────┘  └──────────────────┘  └──────────────────┘  └─────────────────────────────────┘
```

---

## The Order Table

Each row shows:

| Column | What it shows | Notes |
|---|---|---|
| Order # | INV-1-20260515-0042 | Monospace, gold colour |
| Date / Time | 15 May 26 / 20:45 | Two lines |
| Location | T4 or Token #42 or Walk-in | — |
| Source | Dine-in, Zomato, QR etc. | With coloured icon |
| Staff | First name of waiter who took order | "—" for QR/aggregator orders |
| Items | Count of items | — |
| Total | Rs.395 | Right-aligned, bold |
| Paid via | CASH UPI CARD chips | Multiple if split payment |
| Status | Closed / Cancelled / Billing | Colour-coded pill |

Cancelled orders are shown **struck-through** so they stand out but are still visible for reconciliation.

Click any row → slide-in detail panel opens from the right. No page reload.

---

## The Detail Panel

Opens when you click any order row. Slides in from the right.

```
┌─────────────────────────────────────────────────────┐
│ Order Detail                                    [×] │
│ INV-1-20260515-0042                                 │
├─────────────────────────────────────────────────────┤
│ ORDER INFO                                          │
│ Date          15 May 2026, 20:45                    │
│ Location      Table T4                              │
│ Source        Dine-in                               │
│ Waiter        Ravi Kumar                            │
│ Customer      Ananya · 9876543210                   │
│ Duration      33 min                                │
│ Status        CLOSED ●                              │
├─────────────────────────────────────────────────────┤
│ ITEMS (4)                                           │
│ 2× Paneer Tikka                         Rs.360      │
│ 1× Dal Makhani  [VOID]                  Rs.200      │
│    Reason: Customer changed mind                    │
│ 2× Butter Naan                          Rs.80       │
│ 1× Cold Coffee  [COMP]                  Rs.0        │
├─────────────────────────────────────────────────────┤
│ TOTALS                                              │
│ Subtotal                                Rs.440      │
│ GST (5%)                                Rs.22       │
│ Discount (Staff 10%)                   -Rs.46       │
│ ─────────────────────────────────────────────────── │
│ Grand Total                             Rs.416      │
├─────────────────────────────────────────────────────┤
│ PAYMENTS                                            │
│ CASH · Ref: —                   Rs.300  20:48       │
│ UPI  · Ref: —                   Rs.116  20:48       │
├─────────────────────────────────────────────────────┤
│ AUDIT TRAIL  (owner/manager only)                   │
│ 20:45  Order created           Ravi                 │
│ 20:52  Dal Makhani voided      Priya (Manager)      │
│ 20:55  Discount applied        Priya (Manager)      │
│ 20:58  Cold Coffee comped      Priya (Manager)      │
│ 21:08  Payment collected       Priya (Manager)      │
├─────────────────────────────────────────────────────┤
│ [ 🖨 Reprint Receipt ]                              │
└─────────────────────────────────────────────────────┘
```

Press **ESC** or click outside the panel to close it.

---

## The CSV Export

`/history/export/` — same filters as the main view apply.

**Columns:**
```
Order #, Date, Time, Location, Source, Waiter, Items,
Subtotal (Rs), GST (Rs), Discount (Rs), Total (Rs),
Cash (Rs), UPI (Rs), Card (Rs), Refund (Rs),
Status, Duration (min)
```

**Limits:**
- Maximum 2,000 rows per export
- If your filter has more than 2,000 results, the first 2,000 are exported and the header row includes a note: `"Showing 2000 of 5432 orders"`
- Refund rows appear as negative amounts in the Refund column
- Open orders are included if you set Status = "Everything incl. open"

---

## Edge Cases — What Happens When Things Are Unusual

| Situation | What you see |
|---|---|
| Menu item deleted after the order was placed | Shows "Deleted Item" with the price it was charged at. History never loses data. |
| Order placed via QR menu (no cashier) | Staff column shows "—", Source shows "QR Menu" |
| Order placed via Zomato/Swiggy webhook | Staff shows "—", Source shows "Zomato" or "Swiggy" |
| Customer paid with Cash + UPI (split bill) | Both pills shown in "Paid via" column. Both rows in detail panel. |
| Order fully cancelled | Shows struck-through row. Still appears in history for audit purposes. |
| Refund was issued | Appears in payments section in RED with a minus sign. |
| Fully complimentary order (Rs.0) | Shows "COMP" pill. Grand total 0. Visible in filter using "Complimentary" method. |
| Waiter tries to access another waiter's order URL | Gets 403. The role check is server-side, not just UI. |
| Cashier tries to see orders older than 30 days | Gets 403 if they manually type an old order ID. |
| Very long void reason text | Truncated in the panel, full text in the audit trail. |
| Order has no events in audit trail | Audit trail section simply doesn't appear. |
| Customer name/phone not recorded | Those rows don't appear in the detail panel. |

---

## For the Restaurant Owner — Practical Use Cases

**End of day reconciliation:**
Filter by today → check total matches your cash drawer + UPI app totals.

**Investigating a dispute:**
Search by order number or customer phone → open detail → see exact items, who applied discounts, who voided what.

**Staff performance:**
Filter by Staff = "Ravi" → see how many orders he took today, average order value, any voids or discounts applied.

**Checking aggregator orders:**
Filter by Source = "Zomato" → see all Zomato orders, their amounts, which were cancelled.

**Monthly export for accountant:**
Set date range to last month → Export CSV → send to accountant. Columns match what most accountants need for reconciliation.

**Finding a lost receipt:**
Customer says they need a copy. Search their phone number → find the order → Reprint Receipt → thermal receipt page opens → print.

---

## Keyboard Shortcut

Press **ESC** anywhere on the history page to close the detail panel.

---

## What the Audit Trail Records

Every significant action on an order is logged automatically:

```
order_created         → when the order was first opened
item_added            → when items were added (with which items)
item_voided           → when an item was voided (with the reason)
item_updated          → when quantity or price was changed
kot_sent              → when KOT was sent to kitchen
discount_applied      → when a discount was applied (with percentage/amount)
payment_added         → when a partial payment was recorded
payment_completed     → when order was fully paid and closed
payment_refund_*      → when a refund was requested, approved, or rejected
table_transferred     → when order was moved to a different table
tables_merged         → when tables were merged
order_cancelled       → when the entire order was cancelled
```

All entries show: time, event type, and who performed the action.
Cashier and waiter roles do NOT see this trail — only owner and manager.
