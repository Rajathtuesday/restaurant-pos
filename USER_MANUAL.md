# Rasova — User Manual

### A plain-English guide to running your restaurant on Rasova

---

## Table of Contents

1. [Welcome](#1-welcome)
2. [Logging In](#2-logging-in)
3. [For Owners & Managers](#3-for-owners--managers)
4. [For Cashiers (QSR / Counter)](#4-for-cashiers-qsr--counter)
5. [For Waiters (Fine Dining)](#5-for-waiters-fine-dining)
6. [For Kitchen Staff](#6-for-kitchen-staff)
7. [Customer Self-Ordering (QR Menu)](#7-customer-self-ordering-qr-menu)
8. [Inventory Basics](#8-inventory-basics)
9. [Reports & End of Day](#9-reports--end-of-day)
10. [When Things Go Wrong](#10-when-things-go-wrong)
11. [Glossary](#11-glossary)

---

## 1. Welcome

Rasova is the software that runs your restaurant's day-to-day: taking orders, sending them to the kitchen, printing bills, tracking stock, and telling you at the end of the night exactly how much money came in.

You don't need to know anything technical to use it. This manual is written for the people actually working the floor and the counter, not developers.

Every screen in Rasova is built around one rule: **big buttons, plain words, nothing hidden.** If something feels confusing, that's a mistake in the software, not something you're missing.

---

## 2. Logging In

- Go to your restaurant's Rasova web address (given to you when your account was set up).
- Enter your **username** (not your email) and your password.
- Type your username in **lowercase**, exactly as it was given to you — logins are case-sensitive, so `Vishal` and `vishal` are treated as different usernames.

**What you see after logging in depends on your role:**

| Role | What they see |
|---|---|
| **Owner** | Everything — reports, staff, setup, all outlets |
| **Manager** | Almost everything, except a few owner-only settings |
| **Cashier** | Billing, payments, cash register |
| **Waiter** | Tables, orders, kitchen status |
| **Chef** | Kitchen display only |

If you try to open a page that isn't meant for your role, Rasova will simply take you back to your own dashboard — nothing breaks, nothing is exposed.

---

## 3. For Owners & Managers

### Setting Up Your Restaurant

The first time you log in, Rasova walks you through a short setup wizard:

1. **Menu** — add your categories and dishes, or photograph your existing paper menu and let Rasova's AI import it automatically (Settings → Menu → AI Import).
2. **Tables** (fine dining) — bulk-create tables in one click, or add them one at a time.
3. **Staff** — create logins for your team (see below).
4. **Payment methods** — turn on Cash, UPI, and/or Card, and customize how each one is labeled on the bill.
5. **Printer** — connect your kitchen and billing printers.

You can revisit any of these later from the **Setup** menu — nothing here is a one-time-only step.

### Adding Staff

Go to **Setup → Staff**. Fill in a username, password, role, and outlet, then click **Add Staff**. That's it — they can log in immediately.

### Resetting a Staff Member's Password

If someone forgets their password or gets locked out after too many wrong attempts:

1. Go to **Setup → Staff**.
2. Find their name in the list and click **Reset Password**.
3. Type a new password (at least 8 characters) and confirm.

This also automatically clears any login lockout on their account, so there's nothing else to do afterward. You never need anyone's technical help for this.

### Removing a Staff Member

Click **Deactivate** next to their name instead of trying to delete them. Deactivating:

- Immediately blocks them from logging in, even if they're already logged in somewhere right now
- Keeps every order, shift, and cash-register record they were ever part of, exactly as it was
- Can be undone any time by clicking **Reactivate**

By default, the staff list only shows active team members. Tick **Show inactive** at the top of the list if you need to see former staff.

### Cash Registers (Shifts)

Before taking any payments, a cash register session needs to be open. Fine dining staff open one manually from **Shifts → Cash Sessions**. QSR/counter restaurants open one automatically the first time a payment is taken each day.

At the end of a shift, count the actual cash in the drawer and close the session — Rasova will show you the difference between what it expected and what you counted, so any shortfall is caught immediately, not days later.

---

## 4. For Cashiers (QSR / Counter)

1. Tap items on the menu to add them to the current order.
2. Tap **Checkout**.
3. Choose the payment method and enter the amount.
4. Tap **Confirm & Print Slip**.

The screen automatically resets after a couple of seconds, ready for the next customer.

**If the internet drops mid-service:** cash payments still work — a banner will tell you the payment was saved and will sync automatically the moment the connection comes back. UPI and card payments cannot be taken while offline, since those need to be verified with the bank or payment app in real time — switch to cash for those customers until the connection returns.

---

## 5. For Waiters (Fine Dining)

1. Tap a table on the floor plan to open it.
2. Add items, then tap **Send to Kitchen**.
3. Watch the item status change from *Preparing* to *Ready* — that's your cue to serve it.
4. Tap **Serve** once the food is delivered to the table.
5. When the table is ready to pay, generate the bill and hand it to the cashier, or close it yourself if you have billing access.

**Table colors, at a glance:**

| Color / State | Meaning |
|---|---|
| Free | Nobody seated |
| Ordering | Guests are still choosing / order not yet sent |
| Preparing | Kitchen is cooking |
| Ready | Food is ready to serve |
| Billing | Bill has been generated, awaiting payment |
| Cleaning | Table just vacated, needs to be reset |

**Canceling an item:** tap **Cancel** on the item. If it was already sent to the kitchen, any ingredients already deducted from stock are automatically put back — you don't need to adjust inventory by hand.

---

## 6. For Kitchen Staff

The kitchen display shows every item currently in progress, grouped by station if your restaurant uses multiple stations (grill, tandoor, dessert, etc.).

- Tap an item once it's started to mark it **Preparing**.
- Tap it again once it's done to mark it **Ready** — this is what tells the waiter it's time to serve.

If a dish will take longer than expected, use the **Kitchen Message** button to send a note straight to the waiter's screen ("Delayed 15 mins") instead of shouting across the restaurant.

---

## 7. Customer Self-Ordering (QR Menu)

Each table has its own printed QR code. When a guest scans it:

- They see your full menu, with photos, veg/non-veg markers, and search.
- They can add items to a cart and place the order directly — it appears on your staff dashboard for approval before it goes to the kitchen.
- Once their order is placed, a small status button appears on their screen. Tapping it shows a live Received → Preparing → Ready → Served timeline, so guests can check progress themselves instead of having to ask a waiter.
- They can tap **Call Waiter** to get a staff member's attention without raising a hand.

**Important:** each table's QR code is unique and secret. A guest can only order onto the table they actually scanned — there is no way to guess or type in a table number to reach a different one.

---

## 8. Inventory Basics

- **Stock** is automatically deducted the moment an item is sent to the kitchen (or at payment time for very simple counter setups).
- **Wastage** — if something is dropped, spoiled, or over-poured, log it under **Inventory → Wastage** so your stock numbers stay accurate.
- **Low stock alerts** — set a threshold per ingredient, and Rasova will flag it once stock drops below that line.
- **Purchase Orders** — create a PO when you're restocking from a supplier; receiving it automatically adds the quantity back to stock.

---

## 9. Reports & End of Day

**Owner Dashboard** — a live view of today's revenue, order count, active tables, and low-stock items, updating automatically through the day.

**Z-Report** (Shifts → Cash Sessions → Export) — the official end-of-night report for counting the till. It correctly understands your restaurant's actual business day, so if you're open past midnight, a dinner service that runs into the early hours is still counted as one continuous night, not split across two separate days.

**Other reports** (Reports menu) — daily/hourly sales, top-selling items, category and table performance, staff performance, and inventory usage, all filterable by date range and by outlet if you run more than one location.

---

## 10. When Things Go Wrong

**"Failed" or an error message you don't recognize appears mid-action** — reload the page and try again. Rasova now shows a plain "Your session needs a refresh" message instead of a confusing technical error for exactly this situation.

**A table is stuck showing the wrong status** — this should no longer happen after recent fixes, but if it ever does, canceling and re-adding an item on that table will force it to recalculate correctly.

**The internet goes down** — new orders and cash payments both keep working and will sync automatically once the connection returns; you'll see a banner at the top of the screen showing how many actions are waiting to sync. UPI and card payments are the only things that require a live connection.

**Locked out after too many wrong password attempts** — ask your owner or manager to reset your password from **Setup → Staff**; it also clears the lockout automatically.

**A printer isn't printing** — check that the local printer agent/app is running on the device connected to your printer; a browser print popup will appear as a fallback if it can't be reached.

---

## 11. Glossary

| Term | Meaning |
|---|---|
| **KOT** | Kitchen Order Ticket — the slip sent to the kitchen listing what to cook |
| **QSR** | Quick Service Restaurant — counter-style ordering, no table service |
| **Z-Report** | The official end-of-night report used to count and close the cash register |
| **GST / CGST / SGST** | India's Goods and Services Tax, split into two equal halves on your bill |
| **SAC Code** | The tax code (996331) required on restaurant bills for GST compliance |
| **Business Day** | Your restaurant's actual operating day, which may extend past midnight — set by your closing-hour cutoff in Outlet settings |
| **Cash Session** | The record of a cash register being opened, used, and closed for a shift |
| **Void** | Canceling an item or order after it's been placed |
| **Complimentary** | Marking an item as free (₹0) without discounting the whole order |

---

*Questions not covered here? Ask your restaurant's Rasova administrator, or reach out to Rasova support.*
