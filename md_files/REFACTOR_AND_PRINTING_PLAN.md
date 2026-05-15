# Phase 2 Mastery: Refactor, Performance & Printing Plan

This document outlines the strategic roadmap for migrating the POS system from an MVP state into a hardened, highly performant, enterprise-grade architecture.

---

## 1. Unified Templates & Dynamic CSS Theming

### The `base.html` Architecture
Currently, every template (billing, tables, dashboard) re-declares its own `<head>`, CSS links, JS imports, and headers.
**The Plan:**
1. Create `core/templates/core/base.html`.
2. Move all boilerplate (Bootstrap, Fonts, Theme Toggle logic, global CSS variables) into this base template.
3. Every page will use `{% extends "core/base.html" %}` and inject specific content via `{% block content %}`.

### Editable CSS (Customer Choice Theming)
Different restaurants want their POS to match their brand colors (e.g., Green for Vegan, Gold for Fine Dining).
**The Plan:**
1. Add `primary_color` (Hex) and `font_family` to the `Tenant` model.
2. In `base.html`, output a dynamic `<style>` block:
```css
:root {
    --accent-gold: {{ request.user.tenant.primary_color|default:"#c5a059" }};
    font-family: '{{ request.user.tenant.font_family|default:"Outfit" }}', sans-serif;
}
```
**The Impact:** Changing a color in the Django Admin instantly updates the entire POS UI (buttons, active states, borders) without touching CSS files.

---

## 2. Robust JS Error Handling & State Management

**The Current State:** Raw `fetch` calls, naked `alert()` popups, and swallowing errors.
**The Plan:**
1. **Global Fetch Wrapper:** Create a single JS utility `apiClient.js` that handles CSRF tokens and intercepts 400/500 level errors.
2. **Toast Notifications:** Replace `alert()` with a modern Toast UI (e.g., SweetAlert2 or Bootstrap Toasts) for non-blocking error messages.
3. **Optimistic Updates:** UI should update instantly when an item is added to the cart, but revert gracefully if the backend request fails.
4. **Try/Catch Blocks:** Wrap all async logic.
```javascript
try {
    const response = await apiClient.post('/order/add/', payload);
    updateCartUI(response.data);
} catch (error) {
    showToast("Failed to add item: " + error.message, "danger");
    revertCartUI(); // Auto-heal
}
```
**The Impact:** The POS will feel 10x faster. When network drops happen in a busy restaurant, the UI won't freeze silently; the cashier will instantly know what failed.

---

## 3. Performance, Celery & Redis

**The Current State:** Everything happens synchronously in the Django HTTP request lifecycle. 
- Generating a 30-day Z-Report locks the thread.
- `Order.recalculate_totals()` runs heavily on the main thread.

**The Plan (Adding Celery & Redis):**
1. **Redis:** Acts as a rapid message broker and cache. We will cache Menu items (which rarely change) so the billing screen loads in 50ms instead of 300ms.
2. **Celery:** Offload heavy tasks. When a user requests an Excel export, Django immediately returns "Report is generating..." and Celery builds the file in the background, pinging the frontend via WebSocket when ready.
**The Impact:** Your Gunicorn workers will never time out. The POS will be able to handle hundreds of concurrent waiters without database lock contention.

---

## 4. Thermal Printing Plan (Receipts & KOTs)

Printing from a cloud web app to local USB thermal printers is notoriously difficult due to browser security blocking direct hardware access.

### Step 1: The Kitchen Order Ticket (KOT) Network Printers
Kitchens usually have Ethernet/WiFi thermal printers.
- **Tech:** Python `python-escpos` library.
- **How it works:** When a waiter clicks "Send to Kitchen", the Django backend uses the printer's IP address to send raw ESC/POS commands directly over TCP/IP.
- **Benefit:** Instant, silent printing. No browser print dialog pops up.

### Step 2: The Billing Desk (USB/Bluetooth)
For the cashier's local receipt printer attached via USB.
- **Option A (Browser Print):** Continue using `@media print` with an 80mm constrained CSS layout. The cashier hits "Print", the browser dialog opens, they press Enter. (Current method—reliable but requires 1 manual click).
- **Option B (Print Node / QZ Tray):** Install a small daemon (QZ Tray) on the billing computer. The web app sends raw ESC/POS data to `localhost:8182`, bypassing the browser print dialog completely. 
- **Plan:** Implement **Option A** with perfect 80mm/58mm CSS tuning first. Provide **Option B** as an "Enterprise Plugin" for 1-click silent printing.

### ESC/POS vs HTML Printing
We will format receipts natively. Thermal printers suck at rendering HTML/Images. Raw ESC/POS text prints instantly and perfectly cuts the paper. 

**Next Steps?** Let me know if you want to start by building the `base.html` architecture, or if you want to start writing the Thermal Printing integration!
