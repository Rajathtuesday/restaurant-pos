# How to Use Rasova POS
**For restaurant owners, managers, cashiers, waiters, and kitchen staff.**

---

## Quick reference by role

| You are | Go to | Do this |
|---|---|---|
| Owner / Manager | `/dashboard/` | See today's revenue, voids, open tables |
| Cashier | `/billing/` | Take orders, collect payment, print bill |
| Waiter | `/billing/?table=X` | Add items to a table's order |
| Kitchen | `/kitchen/` | See KOTs, mark items ready, message waiters |
| Manager (floor) | `/tables/` | See all tables, urgency alerts, transfers |

---

## First-time setup (under 27 minutes)

When you log in for the first time you will be redirected to the setup wizard.
Follow the 5 steps in order:

**Step 1 — Restaurant info** (~3 min)
- Enter your restaurant name, type (fine dining / QSR / café), phone, GSTIN, address
- Upload your logo — it appears on every printed bill
- GSTIN is required by law on bills. If you are not GST registered, leave it blank.

**Step 2 — Menu** (~3 min, or 60 seconds with AI import)
- Click **AI Import** → take a photo of your paper menu or PDF → upload it
- Rasova reads the photo and imports all items automatically (35 items in ~60 seconds)
- Or click **Load sample menu** to get 8 starter items you can edit
- Or type items manually: one category and up to 3 items to get started

**Step 3 — Staff** (~2 min)
- Create a login for your cashier or manager
- They use this username + password to log in on any device (tablet, laptop, phone)
- Skip this step if you are setting up alone and will add staff later

**Step 4 — Tables** (~1 min for fine dining, skipped for QSR)
- Type how many tables you have (e.g. 12) and a prefix (e.g. T)
- Rasova creates T1, T2 … T12 automatically
- For QSR: this step is skipped — you use token numbers instead

**Step 5 — Payment methods** (~1 min)
- Toggle Cash, UPI, and Card on or off
- Enter your UPI ID (e.g. `yourrestaurant@okaxis`) to show a scannable QR on bills
- This is separate from a payment gateway — it just prints the QR for customers to scan

After finishing, you land on the dashboard. A **Setup checklist** appears in the bottom-right
corner showing which of the 5 steps are actually complete. Click any item to go fix it.

---

## Taking an order (fine dining)

1. Go to `/tables/` (Floor Plan)
2. Click a green (free) table → a side panel opens
3. Click **New Order** or go directly to `/billing/?table=1`
4. On the billing screen, search for items or scroll through categories
5. Click an item to add it. Click again to increase quantity.
6. When ready, click **Send to Kitchen** — this prints a KOT and alerts the kitchen display
7. Add more items at any time. Each new batch sent creates a new KOT.
8. When the customer is ready to pay, click **Generate Bill**
9. Select payment method (Cash / UPI / Card) → click **Collect Payment**
10. The bill prints automatically (bill first, then KOTs)

---

## Taking an order (QSR / counter)

1. Go to `/billing/` directly (no table needed)
2. A token number is assigned automatically when you start
3. Add items, click **Send to Kitchen**
4. The kitchen display shows the token number
5. When food is ready, the display shows it as "Ready"
6. Customer collects, you close the order with **Collect Payment**

---

## The kitchen display

Open `/kitchen/` on a tablet or monitor in the kitchen.

Each card is one KOT (kitchen order ticket):
- **Token / Table number** at the top
- Items listed below with quantities
- A **timer** shows how long ago the order came in
- Cards turn **orange after 15 minutes**, **red after 30 minutes**

Actions on each card:
- **START** — marks all items as "preparing" (optional, for tracking)
- **BUMP** — removes the card from the screen (marks order as served)
- **Message Waiter** — sends a message to the waiter who placed the order
  (use this for "Delayed 15 mins" or "Item out of stock")

The kitchen display auto-refreshes every 5 seconds. No need to reload.

---

## Sending a message from kitchen to waiter

1. On the kitchen display, click the **envelope icon** on any KOT card
2. Choose a quick message (Delayed 5m, Delayed 15m, Out of stock)
   or type a custom message
3. Click **Send**
4. The waiter gets a notification toast on their screen within 8 seconds

Only the waiter who placed that order sees the message (not all waiters).

---

## The floor map

Open `/tables/` to see all tables at a glance.

**Colours:**
- Green — free, no active order
- Yellow — ordering (items added but not yet sent to kitchen)
- Orange — preparing (KOT sent, kitchen working)
- Blue — served (food delivered, waiting for bill)
- Cyan — billing (bill generated, awaiting payment)
- Pink — cleaning (payment done, table being cleared)
- Purple — merged with another table

**Alert strip at the top:**
- Shows how many tables are occupied vs free
- Flags tables stuck over 30 minutes (someone forgot to close an order)
- Flags tables needing approval (QR menu orders waiting for cashier review)

**Actions when you click a table:**
- Add items, generate bill, transfer to another table, merge tables, mark clean

---

## Voiding an item

You can void (cancel) an item after it has been sent to kitchen:
1. On the billing screen, find the item
2. Click the **X** or **Void** button next to it
3. Select a reason (Kitchen error, Customer changed mind, etc.)
4. The item is removed from the bill and marked void in the kitchen

Void items are tracked in the daily report under the manager's name.
Managers and owners can see all voids; waiters cannot void items they didn't add.

---

## Applying a discount

1. On the billing screen, click **Discount**
2. Choose percentage (e.g. 10%) or flat amount (e.g. ₹50)
3. The discount appears on the bill and in the day's report
4. Only managers and owners can apply discounts (cashiers cannot)

---

## Printing

**Bill + KOTs print automatically** when you collect payment.
The sequence is:
1. Bill (customer copy) → full cut → paper separates
2. KOT 1 → partial cut (stays connected)
3. KOT 2 → partial cut
4. (Kitchen tears off the chain and distributes to stations)

**To reprint a bill manually:**
- On the bill screen (`/bill/<order_id>/`) click **Print Bill**

**To test your printer without a real order:**
- Run: `python manage.py preview_print --list` to see recent orders
- Run: `python manage.py preview_print <order_id>` to see what would print

**Printer not responding:**
- Check that the printer IP in Setup → Kitchen Stations matches your printer's IP
- Make sure the printer and the server are on the same Wi-Fi network
- Run a test print from Setup → Kitchen Stations → Test Print button

---

## Daily reports

Go to `/reports/` to see:
- **Today's sales** — total revenue, order count, average order value
- **Sales by item** — which dishes sold most today
- **Voids and discounts** — everything voided or discounted, by whom
- **Payment breakdown** — cash vs UPI vs card split
- **Waiter performance** — orders by staff member

The reports reset at the business day start hour (set in outlet settings, default 6am).
If your restaurant closes at 2am, orders from midnight to 6am count as the previous day.

---

## Managing your menu

Go to `/menu/` to:
- Add or edit items — click the pencil icon on any row
- Toggle **Veg / Non-veg** — the coloured square dot on each row shows current status
- Toggle item availability — click the toggle icon to take an item off the menu temporarily
- Change item price — click the pencil, update price, save
- Add a photo to an item — click edit, upload image
- Assign items to kitchen stations — for multi-station kitchens (Grill, Fryer, etc.)

**AI Menu Import:**
- In Menu Management, click **AI Import** in the top bar
- Upload a photo or PDF of your menu
- Rasova reads it and creates all categories and items automatically
- Review and edit anything that came through incorrectly

---

## Managing staff

Go to `/accounts/staff/` to:
- Add new staff with username, password, and role
- Change a staff member's role
- Deactivate a staff member (they cannot log in but their order history is preserved)

**Roles and what they can do:**

| Role | Take orders | Collect payment | Apply discount | See reports | Setup |
|---|---|---|---|---|---|
| Owner | Yes | Yes | Yes | Full | Yes |
| Manager | Yes | Yes | Yes | Full | Yes |
| Cashier | Yes | Yes | No | Today only | No |
| Waiter | Yes | No | No | No | No |
| Kitchen | No | No | No | No | No |

---

## Waiter call button

If your restaurant has QR menus at tables, customers can tap **Call Waiter** on the QR menu.
The waiter assigned to that table gets a notification with the table number.

To resolve a call: on the waiter dashboard (`/waiter/`), click **Resolved** next to the table.

---

## Common problems and fixes

**"Order not showing in kitchen"**
→ Make sure you clicked **Send to Kitchen** on the billing screen, not just added items.
Items in the cart do not appear in the kitchen until you send them.

**"Bill shows wrong amount"**
→ Check if a discount or complimentary item was applied. Go to the order and review line items.

**"Table shows as free but it has customers"**
→ The order may not have been created properly. Check `/billing/?table=X` — if no open order
exists, the table will show free. Start a new order.

**"Printer not cutting paper"**
→ Set the cut type to "Partial" in Setup → Kitchen Stations if the printer's cutter is jamming.
Or set it to "None" if the printer has no cutter (staff tear manually).

**"I can't log in"**
→ Ask your owner or manager to reset your password from `/accounts/staff/`.
If the owner can't log in, contact Rasova support.

**"The app is slow on the tablet"**
→ The kitchen display and floor map refresh every 5 seconds. On slow networks this can lag.
Make sure the tablet is on Wi-Fi, not mobile data.

---

## URL quick reference

| Page | URL | Who uses it |
|---|---|---|
| Dashboard | `/dashboard/` | Owner, Manager |
| Billing / Order screen | `/billing/` | Cashier, Waiter |
| Floor plan | `/tables/` | Manager, Cashier |
| Kitchen display | `/kitchen/` | Kitchen staff |
| Reports | `/reports/` | Owner, Manager |
| Menu management | `/menu/` | Owner, Manager |
| Staff management | `/accounts/staff/` | Owner |
| Setup / Configuration | `/setup/` | Owner |
| Waiter dashboard | `/waiter/` | Waiter |
| QR digital menu | `/menu/digital-menu/?table_token=XXX` | Customer |
