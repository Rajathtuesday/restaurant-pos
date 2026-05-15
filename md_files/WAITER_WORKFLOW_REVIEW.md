# 🏃 Waiter Workflow & Communication Architecture
**Project:** Fine Dining POS
**Focus:** Table Ownership & Kitchen-to-Waiter Alerts

---

## 1. The Problem: "The Blind Waiter"
In a busy restaurant, chaos happens when communication breaks down. Right now, your POS suffers from two common blind spots:
1. **The Double-Service Problem:** Two waiters might walk up to Table 4 because neither knows who "owns" it.
2. **The Cold Food Problem:** The kitchen finishes a dish, but the waiter doesn't know unless they actively open the "Running Orders" screen and check the status. Food sits on the counter and gets cold.

Here is the proposed architecture to solve both problems beautifully.

---

## 2. Table Ownership (The "Claim" System)

### The UI / UX:
On the main `tables.html` grid, if a table has an active order, it shouldn't just be colored red. It should show a **small circular avatar** (e.g., `[ RJ ]` for Rajat, `[ AM ]` for Amit) in the top-right corner of the table card.

### The Database Logic:
- We add `server = models.ForeignKey(User)` to the `Order` model.
- When a waiter opens a new order, they automatically become the `server`.
- If a customer orders via the QR Digital Menu, the table flashes yellow (Unclaimed), and the first waiter to click "Approve" claims the table.

### The "Locking" Rule:
In restaurants, waiters help each other out. We shouldn't *strictly* lock the table (what if Raj goes on a break?). 
**Solution:** If Amit tries to open Raj's table, a quick warning pops up: 
> ⚠️ *"Raj is serving Table 4. Do you want to add items for him?" [Yes] [Cancel]*

---

## 3. "Food Ready" Notifications (The Pickup System)

### The UI / UX:
We do not want the waiter to constantly open the Running Order screen. Instead, we introduce two things:
1. **Global Toast Notifications:** No matter what screen the waiter is on (Dashboard, Menu Management, etc.), a floating banner slides down from the top:
   > 🔔 **Table 4 - Ready for Pickup!**
   > *Paneer Tikka (x2), Garlic Naan (x4)*
2. **The "Pickup" Tab (Bell Icon):** In the main navigation bar, add a Bell icon with a red notification dot (e.g., 🔴 `3`). Clicking it opens a side-drawer showing *only* the food that is currently sitting at the kitchen window waiting to be picked up.

### The Technical Flow:
1. **Chef Action:** Chef clicks "Mark Ready" on the Kitchen Display Screen (KDS) for a specific KOT or Item.
2. **Backend Action:** Django updates the item status to `ready`.
3. **WebSockets / SSE:** The server instantly pushes a message to the Waiter's device. 
4. **Waiter Action:** The waiter sees the notification, grabs the food, and clicks "Mark Served" on their device, clearing the notification.

---

## 4. Implementation Steps (When you are ready)

**Phase 1: Table Avatars (Easy)**
1. Update `Order` model to include `server`.
2. Update `tables.html` CSS to show a small round avatar with the waiter's initials based on `order.server.username`.

**Phase 2: The Notification System (Medium/Hard)**
1. Create a lightweight `Notification` model to track unread alerts.
2. Setup a global JavaScript polling function (or WebSocket) in `base.html` that listens for "Ready" alerts.
3. Build the "Slide-out Pickup Drawer" so waiters have one central place to see all ready food.

---

**Do you approve of this workflow? If yes, we can start with Phase 1 (Table Avatars & Ownership).**
