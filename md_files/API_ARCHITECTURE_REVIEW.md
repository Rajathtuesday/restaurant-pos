# 🚀 Project APIs & Edge Case Review

This document serves as a comprehensive plan and execution strategy for migrating the entire Fine Dining POS system into a headless architecture (Flutter / React Native / React Web support). It includes edge case coverage for all endpoints.

## 🌟 Current API Readiness

Right now, your application is an **HTML-First Django App**. Many of your write endpoints (generating Kot, making modifications, paying a bill) already return excellent `JsonResponse` structures. Your read endpoints (Dashboards, Billing Views, Kitchen Views), however, are heavily coupled with standard HTML template rendering.

To support "Flutter or React Server," we must build a dedicated Stateless JSON API layer. 

### Why Create Separate API Files?
Instead of littering `?format=json` everywhere (which hurts readability and scaling) or making your HTML views confusing, we are creating dedicated `api.py` controllers that have single responsibility: returning high-speed, purely formatted JSON representations.

## 📌 Edge Cases We Must Cover Structurally
1. **Unregistered Tenants:** The API must explicitly validate that the user's `Tenant` and `Outlet` exist and correctly limit the scoping. A flutter request could easily omit an outlet ID.
2. **Offline Data Syncs:** Mobile endpoints need complete `status` identifiers to sync with local SQLite properly. An order could be cached locally as `open` but `paid` on your central server.
3. **Ghost Items (Deleted references):** If a user deletes a `MenuItem` from the admin, `OrderItem`s referencing it can crash the API. We must handle `item.menu_item` safely (returning `Unknown` instead of throwing an `AttributeError`).
4. **Concurrency (Race Conditions):** Two waiters taking an order for the same table. The API must return a clean `{"error": "Table locked"}`.
5. **Decimals Serialization:** JSON doesn't naturally support `Decimal` math. The `api.py` files will serialize using `DjangoJSONEncoder`.

## 🏗️ Execution Blueprint (In Progress)

I am immediately establishing `api.py` files inside the following apps. These APIs will parallel the existing views but skip the templates totally.

### 1. `reports/api.py`
*   `GET /api/reports/dashboard/` -> Overall sales totals, aggregates.
*   `GET /api/reports/kitchen/` -> KOT output, void volumes, top kitchen prep items.
*   **Edge Case Fix:** Zero orders in the selected timespan shouldn't return `null`, it should return `[]` and `0`.

### 2. `menu/api.py`
*   `GET /api/menu/categories/` -> Complete inventory array.
*   `GET /api/menu/items/` -> Nested object representation with variants.
*   **Edge Case Fix:** Hiding explicitly items marked `is_available=False`.

### 3. `orders/api.py`
*   `GET /api/orders/tables/` -> Real-time polling for table availability.
*   `GET /api/orders/active/` -> Full ticket breakdown JSON for active tables.
*   `POST /api/orders/pay/` -> Pure REST port of the payment controller.

Let's apply these explicitly now format them into the primary `urls.py`.
