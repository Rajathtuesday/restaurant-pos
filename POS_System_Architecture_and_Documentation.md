# POS System Architecture & Documentation

## Overview
This is a robust, multi-tenant Django POS system built for restaurant and fine-dining operations. It handles everything from multi-tenant isolation, real-time kitchen order routing, table state management, complex financial tracking, inventory management, and aggregator (Swiggy/Zomato) integrations.

## Application Architecture

### 1. `tenants` (Core Multi-Tenancy)
**Models:**
- `Tenant`: Represents a parent company or restaurant brand. Fields: `name`, `slug` (unique), `timezone`, `logo`.
- `Outlet`: Represents a physical branch. Fields: `tenant`, `name`, `address`.

**Functionality:** Forms the backbone of isolation. Every transaction, order, and menu item belongs to a `Tenant` and an `Outlet`.

### 2. `accounts` (Authentication & Access Control)
**Models:**
- `User`: Custom user model inheriting from `AbstractUser`. Adds `role` (owner, manager, cashier, waiter, chef, agent), `tenant`, `outlet`, `pin_code`, and `phone`.

**Views:**
- `login_view`: Authenticates users and redirects them to their role-specific dashboard. Implements IP-based rate limiting (max 5 attempts).
- `owner_dashboard`: Aggregated metrics for owners.

### 3. `menu` (Catalog & Digital Menus)
**Models:**
- `MenuCategory`: Logical grouping (e.g., Starters, Mains).
- `MenuItem`: The actual product. Fields: `name`, `price`, `is_available`, `is_veg`, `gst_percentage`, `image`.
- `ModifierGroup` & `Modifier`: For customizations (e.g., "Extra Cheese", "Spice Level").

**Views:**
- `digital_menu`: Renders a premium, mobile-first QR menu for self-ordering.
- `customer_submit_order`: Stateless API endpoint for ingesting self-service QR orders.

### 4. `orders` (Core Operations)
**Models:**
- `Table` & `TableMerge`: Tracks physical tables and their states (`free`, `ordering`, `billing`, etc.).
- `Order`: Central financial document. Tracks `subtotal`, `gst_total`, `discount_total`, `grand_total`, and `status`.
- `OrderItem`: Granular line items with dynamic GST and discount tracking.
- `OrderEvent`: Event-sourced audit log for tracking state changes and overrides.
- `Payment`: Financial ledger entries for settled bills.

**Views / API:**
- `billing_view` & `create_order`: POS cart UI and checkout workflow.
- `api_ingest_order`: Webhook ingestion for Zomato/Swiggy. Protected by HMAC signature validation.
- `log_bypass`: Allows managers to close zero-value or skipped payments (capped at 3/day).
- `recalculate_totals`: Optimized backend method for recalculating taxes and discounts without N+1 queries.

### 5. `setup` / `shifts` / `inventory`
- **Setup**: `PaymentConfig` and `AggregatorConfig` manage outlet-specific settings and webhook secrets. `setup_qr_codes` dynamically generates table-specific QR links.
- **Shifts**: `CashSession` tracks physical till balances for cashiers.
- **Inventory**: Tracks raw materials and deduces stock based on recipe definitions.

## Database & Security Paradigms
- **Transactions**: All financial state changes (`pay_order`, `apply_discount`, webhook ingestion) are enclosed in `@transaction.atomic` blocks with `select_for_update()` row-level locking to prevent race conditions.
- **Data Isolation**: Views dynamically filter querysets using `request.user.tenant` and `request.user.outlet`.
- **API Security**: Third-party webhook endpoints validate cryptographic `X-Signature` headers against `AggregatorConfig` secrets.

## Docker Deployment Guide

### Prerequisites
- Docker & Docker Compose
- PostgreSQL (provisioned via Compose)
- Redis (provisioned via Compose)

### Deployment Steps
1. Place `.env` variables (e.g., `SECRET_KEY`, `POSTGRES_DB`).
2. Run `docker-compose up --build -d`.
3. Run migrations: `docker-compose exec web python manage.py migrate`.
4. Create superuser: `docker-compose exec web python manage.py createsuperuser`.

### UI Modernization Architecture
The POS UI utilizes a premium "Fine Dining Monochrome Palette" integrating Google Fonts (`Outfit` and `DM Serif Display`). Key UI elements include:
- **Glassmorphism & Micro-animations**: Modern hover states and smooth transition properties (`cubic-bezier`).
- **Dark Mode**: Integrated local-storage persistent dark mode on dashboards and authentications.
- **Responsive QR Menus**: Mobile-first design focusing on large touch targets, sticky carts, and clear typography.

*(End of documentation)*
