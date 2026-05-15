# Rasova POS — Complete Manual Testing Guide
**Every feature. Every role. Every edge case. Micro-step by micro-step.**

---

## Before you start — read this

Each section tells you:
- **Account** — which login to use
- **Start at** — the exact URL
- **Steps** — numbered, one action per step
- **Expect** — what should happen
- **Pass if** — how you know it worked

Mark each section ✅ Pass or ❌ Fail as you go.

---

## Part 1 — Create All Test Accounts

You need two restaurants and 9 logins. Do this first. Takes 10 minutes.

### 1.1 — Create Fine Dining restaurant

**Account:** Superuser  
**Start at:** `/superuser/`

1. Click inside the "Add a Restaurant" form
2. Restaurant Name → type `Test Fine Dining`
3. Type → select `Fine Dining (table service)`
4. Branch / Outlet Name → type `Main Branch`
5. Phone → type `9876500001`
6. GSTIN → type `27TESTFD00001Z5` (fake, for testing)
7. Owner Username → type `fd_owner`
8. Owner Password → type `test1234`
9. Click **Create Restaurant**
10. **Expect:** green "Created! Redirecting…" message
11. **Expect:** redirects to `/superuser/tenant/<id>/`

On the tenant config page:
12. Scroll to **Printers / Kitchen Stations** section
13. In the "Counter" station row → Printer IP → leave blank (we'll test browser printing)
14. Paper Width → select `80mm`
15. Cut Type → select `Partial`
16. Click **Save Printer**
17. Scroll to **Payment Methods** → tick Cash, UPI → UPI ID → type `testrestaurant@okaxis`
18. Click **Save Payment Config**

### 1.2 — Apply Fine Dining feature preset

Still on `/superuser/tenant/<id>/`:
19. In the "Active Features" section → click **Fine Dining — full table service**
20. Confirm the prompt
21. **Expect:** toast "Applied: Fine Dining — full table service"
22. **Expect:** feature pills refresh — `floor_plan`, `kitchen_display`, `waiter_call` should all be green

### 1.3 — Add staff for Fine Dining

Still on the same page, scroll to **Staff Accounts**:
23. Username → `fd_manager` | Role → Manager | Password → `test1234` → **Add Staff Member**
24. Username → `fd_cashier` | Role → Cashier | Password → `test1234` → **Add Staff Member**
25. Username → `fd_waiter` | Role → Waiter | Password → `test1234` → **Add Staff Member**
26. Username → `fd_waiter2` | Role → Waiter | Password → `test1234` → **Add Staff Member**
27. Username → `fd_kitchen` | Role → Kitchen / Chef | Password → `test1234` → **Add Staff Member**
28. **Expect:** 6 accounts now shown in the staff list (1 owner + 5 staff)

### 1.4 — Create tables for Fine Dining

Log in as `fd_owner` / `test1234`:
29. Go to `/setup/tables/`
30. You will see the table management page
31. Add tables: T1, T2, T3, T4, T5 in section "Main Hall"
32. Add table: Bar1 in section "Bar"

OR use the onboarding wizard:
29. Go to `/setup/onboard/?step=4`
30. Count → `5` | Prefix → `T` → **Create Tables**
31. **Expect:** T1, T2, T3, T4, T5 created
32. Add Bar1 manually from `/setup/tables/`

### 1.5 — Add menu for Fine Dining

Still as `fd_owner`:
33. Go to `/menu/`
34. Click **AI Import** (top right)
35. In the text box type:
    ```
    Starters: Paneer Tikka 180, Veg Spring Roll 120, Chicken 65 220
    Main Course: Dal Makhani 200, Butter Chicken 320, Veg Pulao 180, Fish Curry 280
    Breads: Naan 40, Tandoori Roti 30, Paratha 45
    Beverages: Lassi 80, Soft Drink 60, Water 30, Cold Coffee 120
    Desserts: Gulab Jamun 80, Ice Cream 100
    ```
36. Click **Process with Smart AI**
37. **Expect:** "AI imported X items" success message
38. Reload `/menu/`
39. **Expect:** 5 categories with all items visible

### 1.6 — Create QSR restaurant

**Account:** Superuser  
**Start at:** `/superuser/`

40. Fill the form: Restaurant Name → `Test QSR`
41. Type → `QSR / Fast Food`
42. Outlet Name → `Counter 1`
43. Owner Username → `qsr_owner` | Password → `test1234`
44. Click **Create Restaurant**
45. On the tenant config page → click preset **QSR — no kitchen screen (print strip)**
46. **Expect:** features set — `token_system` ON, `kitchen_display` OFF
47. Add staff: `qsr_cashier` | Cashier | `test1234`

### 1.7 — Add QSR menu

As `qsr_owner`:
48. Go to `/menu/`
49. Click **AI Import** → type or upload a simple menu
50. Or click **Load Sample Menu** on the onboarding wizard step 2
51. Verify items appear

---

## Part 2 — Superuser Panel Tests

**Account:** Superuser  
**Start at:** `/superuser/`

### 2.1 — Control panel loads
1. Navigate to `/superuser/`
2. **Expect:** header says "Rasova Staff / Control Panel"
3. **Expect:** both restaurants listed in the table (Test Fine Dining, Test QSR)
4. **Expect:** type badges visible (Fine Dining, QSR / Fast Food)
5. **Expect:** user count > 0 for both

### 2.2 — Feature flags UI
6. Click **Features** next to Test Fine Dining
7. **Expect:** redirected to `/settings/features/?tenant_id=X`
8. **Expect:** features grouped by category (Ordering & Billing, Kitchen, Inventory…)
9. **Expect:** green toggles for: floor_plan, kitchen_display, waiter_call, kot_system
10. Click toggle on any feature (e.g. `split_bill`)
11. **Expect:** toggle changes colour immediately (no page reload)
12. Reload the page
13. **Expect:** the change persisted (toggle still in new state)
14. Toggle it back to original

### 2.3 — Tenant config page
15. Back to `/superuser/`
16. Click **Setup** next to Test QSR
17. **Expect:** page shows QSR restaurant name in breadcrumb
18. **Expect:** feature pills visible — token_system green, kitchen_display grey
19. **Expect:** staff list shows qsr_owner and qsr_cashier
20. **Expect:** payment config section shows Cash and UPI checkboxes

### 2.4 — Create then delete a test restaurant
21. Back to `/superuser/`
22. Fill form: Name → `DELETE ME`, Type → Fine Dining, Owner → `delete_me_user`, Password → `test1234`
23. Click Create
24. **Expect:** success and redirect to tenant config
25. Go to Django admin: `/admin/tenants/tenant/` → find "DELETE ME" → delete it
26. **Expect:** tenant and its outlet deleted
27. Go back to `/superuser/`
28. **Expect:** "DELETE ME" no longer appears

---

## Part 3 — Login & Role Redirect Tests

Test that each role lands on the right page after login.

### 3.1 — Owner login
1. Log out → go to `/login/`
2. Username `fd_owner` | Password `test1234` → Login
3. **Expect:** redirected to `/dashboard/`
4. **Expect:** owner dashboard visible with revenue strip

### 3.2 — Manager login
5. Log out → login as `fd_manager` / `test1234`
6. **Expect:** redirected to `/dashboard/`

### 3.3 — Cashier login
7. Log out → login as `fd_cashier` / `test1234`
8. **Expect:** redirected to `/billing/`

### 3.4 — Waiter login (fine dining)
9. Log out → login as `fd_waiter` / `test1234`
10. **Expect:** redirected to `/tables/`

### 3.5 — Kitchen login
11. Log out → login as `fd_kitchen` / `test1234`
12. **Expect:** redirected to `/kitchen/`
13. **Expect:** page is in dark mode automatically

### 3.6 — QSR cashier login
14. Log out → login as `qsr_cashier` / `test1234`
15. **Expect:** redirected to `/token/` (token dashboard)

### 3.7 — Wrong password
16. Log out → go to `/login/`
17. Username `fd_owner` | Password `wrongpassword` → Login
18. **Expect:** stays on `/login/` with error message "Invalid username or password"
19. **Expect:** no redirect to dashboard

---

## Part 4 — Fine Dining: Complete Order Flow

Use 4 browser tabs: Owner, Waiter, Kitchen, second Waiter.

### 4.1 — Floor plan loads correctly

**Account:** `fd_owner`  
**Start at:** `/tables/`

1. Navigate to `/tables/`
2. **Expect:** all 6 tables visible (T1-T5 + Bar1)
3. **Expect:** all tables green (free)
4. **Expect:** alert strip at top shows "0 occupied / 6 free" (or similar)
5. **Expect:** section dividers visible: "Main Hall" and "Bar"
6. Check T1 card — should show:
   - Green status bar at top
   - "free" status text
   - "New Order" button
7. **Expect:** no cooking badge, no timer

### 4.2 — Open a table / start an order

**Account:** `fd_waiter`  
**Start at:** `/billing/?table=1` (or click T1 on floor map)

8. Navigate to `/tables/`
9. Click table T1
10. **Expect:** side panel or redirect to `/billing/?table=1`
11. On billing screen: **Expect:** categories visible on left (Starters, Main Course, etc.)
12. **Expect:** empty order on right side
13. **Expect:** "T1" or table name shown in header

### 4.3 — Add items to order

Still on `/billing/?table=1` as `fd_waiter`:

14. Click category "Starters"
15. **Expect:** Paneer Tikka, Veg Spring Roll, Chicken 65 appear
16. Click **Paneer Tikka**
17. **Expect:** item added to cart on right side → quantity 1, price ₹180
18. Click **Paneer Tikka** again
19. **Expect:** quantity becomes 2, price becomes ₹360
20. Click **Veg Spring Roll**
21. **Expect:** added as separate line item
22. Click category "Beverages"
23. Click **Lassi**
24. **Expect:** 3 items in cart total
25. Check subtotal is correct: 2×180 + 120 + 80 = ₹740
26. **Expect:** GST calculated and shown below subtotal

### 4.4 — Send to kitchen (first KOT)

27. Click **Dispatch** (or "Send to Kitchen") button
28. **Expect:** button shows "Dispatching…" briefly
29. **Expect:** cart clears
30. **Expect:** toast "Order Sent" appears

In a different tab — **Account:** `fd_kitchen` at `/kitchen/`:
31. **Expect:** KOT card appears (within 5 seconds, auto-refresh)
32. **Expect:** KOT shows: "T1", "2x [V] Paneer Tikka", "1x [V] Veg Spring Roll", "1x [V] Lassi"
33. **Expect:** timer starts counting up from 0m

Back at `/tables/` — **Account:** `fd_owner`:
34. **Expect:** T1 changes from green → orange (preparing)
35. **Expect:** alert strip shows "1 occupied / 5 free"
36. **Expect:** cooking badge shows "X in kitchen" on T1 card

### 4.5 — Kitchen actions

**Account:** `fd_kitchen` at `/kitchen/`

37. Click **START** on any item in the KOT card
38. **Expect:** item status changes to "Preparing" indicator
39. Click **READY** (or mark-ready button) on an item
40. **Expect:** item shows as ready

41. Test the BUMP button:
    - Click **BUMP** on the KOT card
    - **Expect:** card disappears from kitchen display
    - **Expect:** table T1 on floor map changes colour (toward "ready/served")

### 4.6 — Add more items (second KOT)

**Account:** `fd_waiter` at `/billing/?table=1`

42. Click category "Main Course"
43. Add: Dal Makhani (1), Butter Chicken (1)
44. Click **Dispatch**
45. **Expect:** new KOT appears in kitchen
46. **Expect:** KOT number is higher (e.g. KOT #2)

### 4.7 — Generate bill

**Account:** `fd_cashier` at `/billing/?table=1`

47. Navigate to `/billing/?table=1`
48. **Expect:** running order shown on right side (all items from both KOTs)
49. Click **Bill** button (or **Generate Bill**)
50. **Expect:** redirected to `/bill/<order_id>/`
51. **Expect:** bill shows all items with correct prices
52. **Expect:** GST breakdown visible (CGST + SGST)
53. **Expect:** TOTAL is correct
54. **Expect:** table T1 on floor map turns cyan (billing status)

### 4.8 — Apply discount

Still on `/bill/<order_id>/` as `fd_cashier`:
55. Look for **Discount** button (or section)
56. Click it
57. **Expect:** discount input appears
58. Enter 10 (as percentage) → apply
59. **Expect:** discount of 10% calculated and deducted from total
60. **Expect:** discount line appears in bill summary

*Note: cashier cannot apply discount — if it's blocked, test with `fd_manager`*

### 4.9 — Collect payment (Cash)

61. On `/bill/<order_id>/` → find payment section
62. Select **Cash**
63. Enter amount (enter more than total to test change calculation)
64. Click **Collect Payment**
65. **Expect:** success message
66. **Expect:** change due shown if amount > total
67. **Expect:** bill marked as paid
68. **Expect:** T1 on floor map changes to pink (cleaning)

### 4.10 — Thermal receipt print (browser printing)

69. Click **Thermal Print** button in the bill header
70. **Expect:** new popup/tab opens at `/thermal-receipt/<id>/`
71. **Expect:** receipt formatted correctly (narrow, like a thermal slip)
72. **Expect:** OS print dialog appears automatically
73. Cancel the print dialog (just testing the trigger)
74. **Expect:** popup stays open (it only closes after print)
75. Manually close the popup

### 4.11 — Mark table clean

**Account:** `fd_manager` at `/tables/`

76. T1 should be pink (cleaning)
77. Click T1 card
78. **Expect:** "Mark Clean" button visible
79. Click **Mark Clean**
80. **Expect:** T1 returns to green (free)
81. **Expect:** alert strip updates ("0 occupied / 6 free" again)

---

## Part 5 — Fine Dining: Split Bill

**Account:** `fd_cashier`

1. Open T2 → add 4 items from different categories → Dispatch → Generate Bill
2. Navigate to `/bill/<order_id>/`
3. Find **Split Pay** option (button or section)
4. Split into 2 payments: 50% cash + 50% UPI
5. Enter first payment: Cash, half the total → Collect
6. **Expect:** "Partial payment recorded, remaining: ₹X"
7. Enter second payment: UPI, remaining amount → Collect
8. **Expect:** order fully paid and closed
9. **Expect:** T2 moves to cleaning state

---

## Part 6 — Fine Dining: Void / Cancel

### 6.1 — Void a single item

**Account:** `fd_manager`

1. Open T3 → add 3 items → Dispatch
2. Navigate back to T3 on `/billing/?table=3`
3. In the running order sidebar, find a sent item
4. Click the void/cancel button next to it
5. **Expect:** confirmation prompt: "Are you sure?"
6. Confirm
7. **Expect:** item marked as voided, crossed out
8. Generate bill
9. **Expect:** voided item not in the total
10. **Expect:** bill total is correct without the voided item

### 6.2 — Cancel entire order

**Account:** `fd_manager`

11. Open T4 → add items
12. DO NOT send to kitchen
13. Click **Cancel Entire Order** button
14. **Expect:** confirmation prompt
15. Confirm
16. **Expect:** order cancelled, T4 returns to green
17. **Expect:** toast "Order Cancelled"

### 6.3 — Complimentary item

**Account:** `fd_manager`

18. Open T5 → add 3 items → Dispatch
19. On billing screen, find an item in running order
20. Click the "Complimentary" button on the item
21. **Expect:** item price changes to ₹0
22. Generate bill
23. **Expect:** complimentary item shows ₹0
24. **Expect:** total reduced accordingly

---

## Part 7 — Fine Dining: Table Operations

### 7.1 — Merge tables

**Account:** `fd_manager` at `/tables/`

1. Click **Combine** button (in header)
2. Select Primary Table → T1
3. Select Table to Merge → T2
4. Click Merge
5. **Expect:** T2 card shows "merged" status (purple)
6. **Expect:** T2 shows "Linked to T1"
7. Open `/billing/?table=2`
8. **Expect:** redirected to T1's billing (the primary table)
9. Add items and dispatch
10. **Expect:** KOT shows "T1" (primary table)
11. Return to `/tables/` → click **Combine** → Unmerge T1
12. **Expect:** T2 returns to normal (free, green)

### 7.2 — Transfer order to another table

**Account:** `fd_manager`

13. Open T3 → add items → Dispatch (order now on T3)
14. On `/tables/` → click **Transfer** button
15. Source → T3, Destination → T4 (must be free)
16. Click Transfer
17. **Expect:** T3 becomes free (green)
18. **Expect:** T4 shows active order (orange/preparing)
19. Go to `/billing/?table=4` → **Expect:** same items are there

---

## Part 8 — Kitchen: Messages to Waiter

### 8.1 — Send delay message from kitchen

**Setup:** Open T1, add items, send to kitchen. Keep two tabs open.

**Tab A:** `fd_kitchen` at `/kitchen/`
**Tab B:** `fd_waiter` at any page (notification poller runs)

1. On kitchen display, find the T1 KOT card
2. Click the **envelope icon** on the card
3. **Expect:** message modal appears with quick options
4. Click **Delayed 15m**
5. **Expect:** text field fills with "Delayed by 15 mins"
6. Click **Send**
7. **Expect:** button shows "Sending…" briefly → toast "Message sent to waiter"

In Tab B (waiter's browser):
8. Within 8 seconds (next poll cycle):
9. **Expect:** toast notification appears: "Kitchen (T1): Delayed by 15 mins"
10. **Expect:** badge count on notification icon updates

### 8.2 — Custom kitchen message

11. On kitchen display → click envelope on any KOT
12. Clear the text field
13. Type: "Ingredient out of stock, please inform customer"
14. Click **Send**
15. **Expect:** waiter gets the custom message in toast notification

### 8.3 — Waiter call from QR menu

16. Get the QR code for T2: go to `/setup/tables/` → click QR button next to T2
17. Open the QR link in a browser: `http://yourserver/menu/digital-menu/?table_token=...`
18. Scroll to bottom → find **Call Waiter** button
19. Click it
20. **Expect:** response "Waiter has been notified"

In Tab B (waiter):
21. Within 8 seconds: **Expect:** toast "🔔 Waiter called from T2"
22. **Expect:** notification badge count increases

### 8.4 — Resolve waiter call

23. Navigate to `/waiter/` as `fd_waiter`
24. **Expect:** waiter dashboard shows the pending call for T2
25. Click **Resolved** next to it
26. **Expect:** call disappears from the list
27. **Expect:** badge count decreases on next poll

---

## Part 9 — QSR Complete Flow

**Account:** `qsr_cashier` at `/token/`

### 9.1 — Token dashboard

1. Navigate to `/token/`
2. **Expect:** token counter dashboard loads
3. **Expect:** today's token count visible (starts at 0)
4. **Expect:** "New Order" or token creation button visible

### 9.2 — Create token order and pay

5. Click **New Order** or go to `/billing/`
6. **Expect:** billing screen with no table selector (QSR mode)
7. **Expect:** "PAY & PRINT SLIP" button (green) instead of "Dispatch"
8. Add items: click any category → add 3 items
9. Click **PAY & PRINT SLIP**
10. **Expect:** order is created (creating…)
11. **Expect:** payment modal appears showing Grand Total
12. In the modal:
    - Click **Cash** button
    - **Expect:** button turns gold, amount field fills with total
    - Enter amount (add ₹50 extra to test change)
    - **Expect:** "Change due: ₹50" appears in green
13. Click **CONFIRM & PRINT SLIP**
14. **Expect:** new popup opens `/thermal-receipt/<id>/`
15. **Expect:** popup has TOKEN NUMBER prominently displayed
16. **Expect:** items listed below token number
17. **Expect:** OS print dialog auto-opens
18. Cancel the print dialog
19. **Expect:** full-screen confirmation shows "✓ Paid / Token #X / Printing slip…"
20. **Expect:** after 2.5 seconds, screen auto-resets to blank billing screen
21. **Expect:** token counter on dashboard incremented by 1

### 9.3 — QSR reports check

22. Navigate to `/reports/dashboard/`
23. **Expect:** today's revenue shows the QSR order amount
24. **Expect:** order count = 1 (or more if multiple tests done)

---

## Part 10 — Menu Management

**Account:** `fd_owner` at `/menu/`

### 10.1 — Create a category

1. Navigate to `/menu/`
2. Click **Category** button (top right area)
3. **Expect:** modal or form appears
4. Type category name: `Desserts Test`
5. Click Save
6. **Expect:** category appears in the list
7. **Expect:** category shows 0 items

### 10.2 — Create a menu item (Veg)

8. Click **Add Item** (top right, gold button)
9. **Expect:** item modal opens
10. Name → `Test Gulab Jamun`
11. Price → `85`
12. Category → select `Desserts Test`
13. Description → `Fresh and sweet`
14. Type section → **Veg** radio should be pre-selected (green dot)
15. **Verify:** green [V] radio is checked by default
16. Click **Add to Collection**
17. **Expect:** item appears in Desserts Test category
18. **Expect:** green square dot before item name (veg indicator)

### 10.3 — Create a non-veg item

19. Click **Add Item** again
20. Name → `Test Chicken Wings`
21. Price → `280`
22. Category → `Desserts Test` (for testing, wrong category is fine)
23. Type section → click **Non-Veg** radio (red dot)
24. **Verify:** red [N] radio is now checked
25. Click **Add to Collection**
26. **Expect:** item appears with red square dot (non-veg indicator)

### 10.4 — Edit an item

27. Click the pencil icon on `Test Gulab Jamun`
28. **Expect:** modal opens with existing values pre-filled
29. **Expect:** Name = "Test Gulab Jamun", Price = "85"
30. **Expect:** Veg radio is selected (green)
31. Change price to `90`
32. Click **Refine Selection**
33. **Expect:** price updates to ₹90 in the list

### 10.5 — Toggle item availability

34. Click the toggle icon next to `Test Gulab Jamun`
35. **Expect:** item shows as "Offline" badge (or greyed out)
36. Click toggle again
37. **Expect:** item back to available (Offline badge gone)

### 10.6 — GST management

38. Click **GST** link in the menu navigation
39. **Expect:** GST management page loads
40. Find any category → expand it
41. Click on a GST rate for an item (e.g. change from 5% to 18%)
42. **Expect:** rate updates immediately
43. Change it back to 5%

### 10.7 — Delete an item

44. Click the trash icon on `Test Chicken Wings`
45. **Expect:** item deleted, disappears from list

### 10.8 — Delete a category

46. Find `Desserts Test` category
47. Click the delete button next to it
48. **Expect:** category and all its items deleted
49. **Expect:** `Test Gulab Jamun` also gone (was in that category)

---

## Part 11 — Reports

**Account:** `fd_owner` at `/reports/dashboard/`

1. Navigate to `/reports/dashboard/`
2. **Expect:** page loads with today's date shown
3. **Expect:** revenue chart or summary visible
4. **Expect:** top items listed (items sold today)

### 11.1 — Verify today's sales appear

5. After completing at least 2 orders (from Part 4), check:
6. **Expect:** total revenue matches sum of orders you placed
7. **Expect:** order count matches number of paid orders
8. **Expect:** payment breakdown shows correct cash/UPI split

### 11.2 — Date range check

9. Click on a date picker or previous day
10. **Expect:** report updates to show that day's data

### 11.3 — Export

11. Click **Export** button
12. **Expect:** CSV/Excel file downloads
13. Open the file: **Expect:** data rows with order details

### 11.4 — Cashier cannot see full reports

14. Log out → login as `fd_cashier`
15. Navigate to `/reports/dashboard/`
16. **Expect:** either redirected away or sees limited data (today only)
17. **Expect:** cannot see historical data beyond today

### 11.5 — Waiter cannot see reports at all

18. Log out → login as `fd_waiter`
19. Navigate to `/reports/dashboard/`
20. **Expect:** 403 Forbidden or redirect

---

## Part 12 — Inventory

**Account:** `fd_owner` at `/inventory/board/`

1. Navigate to `/inventory/board/`
2. **Expect:** inventory dashboard loads
3. **Expect:** list of inventory items (if any set up)

### 12.1 — Add inventory item

4. Find the "Add Item" button
5. Name → `Paneer (kg)`
6. Current Stock → `5`
7. Unit → `kg`
8. Low Stock Alert → `1`
9. Save
10. **Expect:** item appears in inventory board

### 12.2 — Link recipe to menu item

11. Go to `/menu/`
12. Find "Paneer Tikka" in Starters
13. Click the recipe icon (book icon, if visible)
14. Or in the item edit modal → Recipe section
15. Link: Paneer (kg), quantity 0.1
16. **Expect:** recipe saved

### 12.3 — Verify auto-deduction

17. Open T1 → add 1x Paneer Tikka → Dispatch
18. Check inventory board `/inventory/board/`
19. **Expect:** Paneer stock reduced by 0.1 (from 5 to 4.9)

---

## Part 13 — Printer Tests

### 13.1 — Preview print (no printer needed)

**Account:** Any (command line)

1. Open terminal in the Rasova project directory
2. Run: `python manage.py preview_print --list`
3. **Expect:** list of recent orders with IDs
4. Pick an order ID (e.g. 142)
5. Run: `python manage.py preview_print 142`
6. **Expect:** formatted receipt printed in terminal
7. **Expect:** `[FULL CUT]` separator after bill section
8. **Expect:** KOT sections after the cut
9. **Expect:** `[partial cut - stays connected]` between KOTs

### 13.2 — Strip mode preview (QSR)

10. Run: `python manage.py preview_print 142 --strip`
11. **Expect:** "Mode: QSR STRIP (token+KOTs connected, one full cut)"
12. **Expect:** bill section → `[partial cut - stays connected]` → KOT → `[FULL CUT - paper tears here]`
13. **Expect:** the receipt uses "TOKEN #" instead of "Bill :" if it's a QSR order

### 13.3 — 58mm paper preview

14. Run: `python manage.py preview_print 142 --width 32`
15. **Expect:** narrower output (32 chars per line instead of 48)
16. **Expect:** text still fits without wrapping badly

### 13.4 — Browser thermal print (requires printer driver)

17. Log in as `fd_cashier`
18. Go to any completed order's bill screen: `/bill/<closed_order_id>/`
19. Click **Thermal Print** button (top right)
20. **Expect:** new popup opens with receipt page
21. **Expect:** receipt formatted for 80mm paper (narrow, monospace font)
22. **Expect:** OS print dialog opens automatically
23. If BillTouch driver installed → select it → print → paper comes out
24. If no driver → cancel the dialog

### 13.5 — QSR payment auto-print

25. Login as `qsr_cashier` → create a new token order → pay
26. **Expect:** popup opens automatically after payment
27. **Expect:** receipt shows large TOKEN NUMBER at top
28. **Expect:** print dialog opens automatically

---

## Part 14 — QR Menu (Customer Facing)

**Account:** No login needed (public page)

### 14.1 — Find a table's QR token

1. Login as `fd_owner` → go to `/setup/tables/`
2. Click the **QR** icon next to T1
3. **Expect:** QR modal opens with a QR code image and link
4. Copy the URL (it contains `?table_token=UUID`)

### 14.2 — Open the digital menu

5. Open the copied URL in a NEW incognito/private window (no session)
6. **Expect:** digital menu loads — restaurant name, logo
7. **Expect:** category tabs visible (Starters, Mains, etc.)
8. **Expect:** items shown as cards with prices
9. **Expect:** veg/non-veg indicators (green/red dots) visible

### 14.3 — Filter veg items

10. Click the **Veg** filter button
11. **Expect:** only veg items shown (non-veg items hidden)
12. Click **All** to reset

### 14.4 — Add to cart and order

13. Click a Veg item → **Expect:** quantity controls appear inline
14. Add 2 of one item, 1 of another
15. **Expect:** sticky cart bar appears at bottom (like Zomato)
16. **Expect:** cart shows "X items — ₹total" and "VIEW CART" button
17. Click **VIEW CART** or **Order**
18. **Expect:** order submitted
19. **Expect:** success message

In staff browser (fd_cashier or fd_manager):
20. Navigate to `/tables/`
21. **Expect:** T1 shows "needs approval" status (yellow-red)
22. **Expect:** alert strip shows "1 need approval"
23. Click T1 → **Expect:** "Approve QR" button visible
24. Click **Approve QR**
25. **Expect:** items approved and sent to kitchen
26. **Expect:** T1 changes to orange (preparing)

### 14.5 — Call waiter from QR menu

27. In the incognito window (customer), scroll to bottom of digital menu
28. Click **Call Waiter** button
29. **Expect:** "A waiter has been notified" message
30. In staff browser within 8 seconds:
31. **Expect:** notification toast "🔔 Waiter called from T1"

---

## Part 15 — Role Permission Boundaries

These tests verify that roles CANNOT do things they shouldn't.

### 15.1 — Waiter cannot collect payment

1. Login as `fd_waiter`
2. Navigate to `/bill/<any_open_order_id>/`
3. Look for payment collection
4. **Expect:** payment buttons absent, or 403 if directly POSTed

### 15.2 — Cashier cannot apply discount

5. Login as `fd_cashier`
6. Open T3, add items, generate bill
7. Try to apply a discount
8. **Expect:** discount button absent or greyed out for cashier role

### 15.3 — Kitchen cannot access billing

9. Login as `fd_kitchen`
10. Navigate to `/billing/`
11. **Expect:** either redirected to `/kitchen/` or shown 403

### 15.4 — Waiter cannot see other outlet's orders

12. Login as `fd_waiter`
13. Try URL manipulation: `/kitchen-data/` 
14. **Expect:** data returned is ONLY for their outlet, not qsr_cashier's outlet

### 15.5 — Cannot cross tenants via URL

15. Login as `fd_cashier`
16. Try: `/bill/<order_id_from_qsr_restaurant>/`
17. **Expect:** 404 (tenant isolation — order doesn't belong to their tenant)

---

## Part 16 — Onboarding Wizard

**Account:** Any fresh owner (use `fd_owner` after clearing session data, or create new)

1. Log out completely → clear browser cookies for localhost
2. Login as `fd_owner`
3. **Expect:** if no menu items, redirect to `/setup/onboard/`

OR: manually go to `/setup/onboard/`:

### Step 1 — Restaurant Info
4. Verify restaurant name pre-filled (Spice Garden or your test name)
5. Change phone number
6. Click **Continue — Add Menu**
7. **Expect:** proceeds to step 2

### Step 2 — Menu
8. **Expect:** two large option boxes: "AI Import" and sample menu
9. Click **Load Sample Menu** button (sample menu form submit)
10. **Expect:** redirected to step 3
11. **Expect:** when you go to `/menu/` — 8 sample items visible

### Step 3 — Staff
12. Enter: username `test_staff`, role Cashier, password `test1234`
13. Click **Continue**
14. **Expect:** step 4

### Step 4 — Tables (Fine Dining)
15. **Expect:** quick create form: count + prefix
16. Count → `6`, Prefix → `T`
17. Click button
18. **Expect:** previews "Will create: T1, T2 … T6"
19. Submit
20. **Expect:** step 5

### Step 5 — Payment Methods
21. Toggle Cash ON, UPI ON, Card OFF
22. UPI ID → `myrestaurant@upi`
23. Click **Finish Setup — Open Dashboard**
24. **Expect:** redirected to `/dashboard/`
25. **Expect:** setup checklist widget in bottom-right corner
26. **Expect:** checklist shows green checkmarks for completed steps

---

## Part 17 — Setup Checklist Widget

**Account:** Any logged-in user

1. Navigate to `/dashboard/`
2. **Expect:** small floating button in bottom-right: "⚙ Setup X/5"
3. Click it
4. **Expect:** panel expands showing 5 checklist items
5. Each item either has green checkmark (done) or grey circle (pending)
6. Click a pending item's link
7. **Expect:** navigates to the right setup page
8. Complete that setup step
9. Return to dashboard
10. **Expect:** that item now shows green checkmark
11. When all 5 are green:
12. **Expect:** checklist widget disappears (or shows "Setup complete!")

---

## Part 18 — Dark Mode & Theme

1. On any page, find the moon/sun icon (top right)
2. Click it
3. **Expect:** entire page switches to dark mode (dark background, light text)
4. **Expect:** no white flash during transition
5. Refresh the page
6. **Expect:** dark mode persists (stored in localStorage)
7. Click again
8. **Expect:** light mode returns
9. On Kitchen display (`/kitchen/`):
10. **Expect:** kitchen is ALWAYS dark regardless of global setting

---

## Part 19 — Offline Banner Test

1. On any page (billing or tables), open browser dev tools (F12)
2. Go to Network tab → click "Offline" toggle
3. **Expect:** red banner appears at top: "You are currently offline"
4. Click "Online" toggle
5. **Expect:** banner disappears

---

## Part 20 — Dashboard Metrics Live Refresh

**Account:** `fd_owner` at `/dashboard/`

1. Open `/dashboard/`
2. Note the current revenue number
3. In another tab, complete a payment (from Part 4 or QSR flow)
4. Return to `/dashboard/`
5. **Expect:** revenue updates within 30 seconds (auto-refresh interval)
6. **Expect:** order count also increments

---

## Quick Reference: All Test Accounts

| Username | Password | Role | Tenant | What they can do |
|---|---|---|---|---|
| (your superuser) | (yours) | Superuser | None | Everything, all tenants |
| fd_owner | test1234 | Owner | Test Fine Dining | Full access to fine dining |
| fd_manager | test1234 | Manager | Test Fine Dining | Everything except setup changes |
| fd_cashier | test1234 | Cashier | Test Fine Dining | Billing + payment only |
| fd_waiter | test1234 | Waiter | Test Fine Dining | Floor + order taking |
| fd_waiter2 | test1234 | Waiter | Test Fine Dining | Second waiter (notification tests) |
| fd_kitchen | test1234 | Chef | Test Fine Dining | Kitchen display only |
| qsr_owner | test1234 | Owner | Test QSR | Full QSR access |
| qsr_cashier | test1234 | Cashier | Test QSR | QSR token billing |

---

## Quick Reference: All URLs to Test

| URL | Who can access | What it does |
|---|---|---|
| `/superuser/` | Superuser only | Create restaurants, manage all tenants |
| `/superuser/tenant/<id>/` | Superuser only | Configure printer, payment, staff for any tenant |
| `/settings/features/?tenant_id=X` | Superuser only | Toggle feature flags per tenant |
| `/dashboard/` | Owner, Manager, Cashier | Revenue dashboard with live metrics |
| `/billing/` | All staff | Main order entry screen |
| `/billing/?table=N` | All staff | Order entry for specific table |
| `/tables/` | Owner, Manager, Waiter | Floor plan with table statuses |
| `/kitchen/` | All staff | Kitchen display (always dark) |
| `/bill/<id>/` | Owner, Manager, Cashier | Bill view for payment collection |
| `/thermal-receipt/<id>/` | Logged-in staff | Browser-based thermal print page |
| `/token/` | QSR roles | Token counter dashboard |
| `/menu/` | Owner, Manager | Menu management |
| `/menu/gst/` | Owner, Manager | GST rate management |
| `/reports/dashboard/` | Owner, Manager | Sales reports |
| `/inventory/board/` | Owner, Manager | Stock levels |
| `/setup/` | Owner | Configuration hub |
| `/setup/onboard/` | Owner | 5-step first-time wizard |
| `/setup/tables/` | Owner | Table management |
| `/setup/kitchen-stations/` | Owner | Printer configuration |
| `/setup/payment-methods/` | Owner | Payment config |
| `/setup/staff/` | Owner | Staff management |
| `/setup/checklist/` | Owner | Setup completion JSON |
| `/waiter/` | Waiter, Manager | Waiter call dashboard |
| `/admin/` | Superuser | Full Django admin |

---

## Test Completion Checklist

Copy this to a spreadsheet. Mark each ✅ or ❌.

```
SETUP
[ ] 1.1  Create fine dining restaurant via /superuser/
[ ] 1.2  Apply fine dining feature preset
[ ] 1.3  Add all staff accounts
[ ] 1.4  Create tables (floor plan)
[ ] 1.5  Import menu via AI importer
[ ] 1.6  Create QSR restaurant
[ ] 1.7  Add QSR menu

LOGIN & ROLES
[ ] 3.1  Owner → dashboard
[ ] 3.2  Manager → dashboard
[ ] 3.3  Cashier → billing
[ ] 3.4  Waiter → tables
[ ] 3.5  Kitchen → kitchen display (dark mode)
[ ] 3.6  QSR cashier → token dashboard
[ ] 3.7  Wrong password → error shown

FINE DINING FLOW
[ ] 4.1  Floor plan loads, tables green, sections visible
[ ] 4.2  Open table / start order
[ ] 4.3  Add items, cart totals correct
[ ] 4.4  Send to kitchen — KOT appears in kitchen display
[ ] 4.4  Table changes colour on floor map
[ ] 4.5  Kitchen can mark items preparing/ready/bump
[ ] 4.6  Second KOT on same table
[ ] 4.7  Generate bill — totals correct, table turns cyan
[ ] 4.8  Apply discount
[ ] 4.9  Collect cash payment with change
[ ] 4.10 Thermal print popup opens auto-prints
[ ] 4.11 Mark table clean — returns to green

QSR FLOW
[ ] 9.1  Token dashboard loads
[ ] 9.2  Create order → pay → popup opens → TOKEN # visible
[ ] 9.2  Print dialog auto-opens, confirmation screen shows 2.5s
[ ] 9.2  Screen auto-resets for next customer
[ ] 9.3  Reports show QSR revenue

SPLIT BILL
[ ] 5.1  Split payment across cash + UPI works

VOID / CANCEL
[ ] 6.1  Void single item — not charged in bill
[ ] 6.2  Cancel entire order — table resets
[ ] 6.3  Complimentary item — shows ₹0 in bill

TABLE OPERATIONS
[ ] 7.1  Merge tables — secondary shows purple/linked
[ ] 7.1  Billing on secondary redirects to primary
[ ] 7.1  Unmerge works
[ ] 7.2  Transfer order to different table

NOTIFICATIONS
[ ] 8.1  Kitchen message → waiter gets toast within 8 seconds
[ ] 8.2  Custom message works
[ ] 8.3  Waiter call from QR → waiter notified
[ ] 8.4  Resolve waiter call works

MENU MANAGEMENT
[ ] 10.1 Create category
[ ] 10.2 Create veg item — green dot shows
[ ] 10.3 Create non-veg item — red dot shows
[ ] 10.4 Edit item — existing values pre-filled, saves correctly
[ ] 10.5 Toggle availability — Offline badge appears/disappears
[ ] 10.6 GST rate change saves and persists
[ ] 10.7 Delete item
[ ] 10.8 Delete category (cascades)

REPORTS
[ ] 11.1 Revenue matches completed orders
[ ] 11.2 Date range works
[ ] 11.3 Export downloads a file with data
[ ] 11.4 Cashier sees limited reports
[ ] 11.5 Waiter cannot see reports

INVENTORY
[ ] 12.1 Add inventory item
[ ] 12.2 Link recipe to menu item
[ ] 12.3 Stock auto-deducts after order with linked item

PRINTER
[ ] 13.1 preview_print shows formatted receipt
[ ] 13.2 --strip flag shows QSR strip mode
[ ] 13.3 --width 32 shows narrower output
[ ] 13.4 Thermal print button on bill opens popup
[ ] 13.5 QSR payment auto-opens print popup

QR MENU
[ ] 14.1 QR link opens without login
[ ] 14.2 Veg filter works
[ ] 14.3 Add to cart, order submitted — appears as "needs approval"
[ ] 14.4 Manager approves — goes to kitchen
[ ] 14.5 Call waiter — waiter notified

PERMISSION BOUNDARIES
[ ] 15.1 Waiter cannot collect payment
[ ] 15.2 Cashier cannot apply discount
[ ] 15.3 Kitchen cannot access billing
[ ] 15.4 Waiter sees only their outlet's data
[ ] 15.5 Cross-tenant order access blocked (404)

ONBOARDING
[ ] 16.1 All 5 wizard steps work
[ ] 16.2 Sample menu loads instantly
[ ] 16.3 Bulk table creation (count + prefix)

SETUP CHECKLIST WIDGET
[ ] 17.1 Widget appears on dashboard
[ ] 17.2 Links navigate to correct setup pages
[ ] 17.3 Widget disappears when all done

DARK MODE
[ ] 18.1 Dark mode toggles correctly
[ ] 18.2 Persists after page refresh
[ ] 18.3 Kitchen always dark

OFFLINE
[ ] 19.1 Offline banner appears when network disconnected
[ ] 19.2 Banner disappears when reconnected

DASHBOARD METRICS
[ ] 20.1 Revenue updates after completing a new order
```

**Total tests: 67**  
Pass all 67 before going live.
