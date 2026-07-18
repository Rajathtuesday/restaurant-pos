# Rasova POS — Market Comparison

> NOTE: I read "7 ships pso" as the **7 leading restaurant POS systems**.
> If you meant something else (e.g. "7shifts" workforce platform, or a
> specific competitor by name), tell me and I'll rewrite this section.

---

## The 7 Competitors

1. **Toast POS** — USA market leader, restaurant-specific, cloud-based
2. **Square for Restaurants** — Small/mid restaurants, easy onboarding
3. **Lightspeed Restaurant** — Europe/Canada leader, analytics-heavy
4. **TouchBistro** — iPad-first, popular in cafes and bars
5. **Revel Systems** — Enterprise franchise chains
6. **Clover POS** — Hardware+software bundle, SME focus
7. **Aloha / NCR** — Legacy enterprise, hotel chains, large QSRs

---

## Side-by-Side Feature Comparison

| Feature                  | Rasova | Toast | Square | Lightspeed | TouchBistro | Revel | Clover | Aloha |
|--------------------------|--------|-------|--------|------------|-------------|-------|--------|-------|
| **Cloud-based**          | ✅     | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ⚠️    |
| **Multi-tenant SaaS**    | ✅     | ✅    | ✅     | ✅         | ❌          | ✅    | ✅     | ❌    |
| **Multi-outlet**         | ✅     | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ✅    |
| **KOT / Kitchen Display**| ✅     | ✅    | ⚠️     | ✅         | ✅          | ✅    | ⚠️     | ✅    |
| **Table Management**     | ✅     | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ✅    |
| **Token / QSR Mode**     | ✅     | ✅    | ❌     | ❌         | ❌          | ✅    | ❌     | ✅    |
| **Split Bill**           | ✅     | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ✅    |
| **Modifiers**            | ✅     | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ✅    |
| **Inventory**            | ✅     | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ✅    |
| **Purchase Orders**      | ✅     | ✅    | ❌     | ✅         | ✅          | ✅    | ❌     | ✅    |
| **CRM / Guest Profiles** | ✅     | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ✅    |
| **Reservations**         | ✅     | ⚠️    | ❌     | ✅         | ✅          | ❌    | ❌     | ⚠️    |
| **Loyalty / Points**     | ⚠️ wip | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ✅    |
| **GST / India tax**      | ✅     | ❌    | ❌     | ❌         | ❌          | ❌    | ❌     | ❌    |
| **AI Menu Import**       | ✅     | ❌    | ❌     | ❌         | ❌          | ❌    | ❌     | ❌    |
| **Role-based Access**    | ✅     | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ✅    |
| **Shift Management**     | ✅     | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ✅    |
| **QR Digital Menu**      | ✅     | ✅    | ✅     | ✅         | ✅          | ✅    | ✅     | ❌    |
| **Waiter Call System**   | ✅     | ❌    | ❌     | ❌         | ❌          | ❌    | ❌     | ❌    |
| **Central Kitchen / Batch Transfer** | ✅ | ❌ | ❌ | ❌     | ❌          | ✅    | ❌     | ✅    |
| **Offline Mode**         | ⚠️ partial | ✅ | ✅  | ✅         | ✅          | ✅    | ✅     | ✅    |
| **Native Mobile App**    | ❌ web | ✅    | ✅     | ✅         | ✅ iPad     | ✅    | ✅     | ❌    |
| **Payment Gateway**      | ❌ wip | ✅    | ✅ built-in | ✅    | ✅          | ✅    | ✅ built-in | ✅ |
| **India / Bharat focused** | ✅  | ❌    | ❌     | ❌         | ❌          | ❌    | ❌     | ❌    |
| **Pricing (India)**      | ₹999–₹5,999 | $110+/mo | $60+/mo | $69+/mo | $69+/mo | $99+/mo | $14+/mo | Enterprise |

✅ = Full feature  ⚠️ = Partial / limited  ❌ = Not available

---

## Where Rasova WINS

### 1. India-first GST compliance
Every competitor listed above is built for the US, Canada, or Europe. None
support GSTIN, HSN codes, GST-categorized items, or GSTR export natively.
**This alone is a moat.** Any Indian restaurant using Toast or Square has to
export to a separate GST filing tool.

### 2. Waiter Call System
No major competitor has a built-in digital waiter call. This is a significant
UX win for dine-in restaurants — completely eliminates the need for a
separate Buzzex or similar device.

### 3. AI Menu Import
No competitor has this. If a restaurant has a PDF menu or a photo of their
chalk board, Rasova can import it. Saves 2–4 hours of setup time.

### 4. QSR + Fine Dining + Franchise in one platform
Most POS systems are either fine-dining OR QSR. Rasova handles both with
the same codebase, plus a franchise/central-kitchen mode. Revel does this
but costs enterprise pricing.

### 5. Price
At ₹999/month (~$12 USD) for Starter, Rasova is 5–10x cheaper than Toast
or Lightspeed for a comparable feature set in the Indian market.

---

## Where Rasova LOSES (Gaps to Close)

### CRITICAL (blocks sales today)

| Gap                          | Impact                              | Effort |
|------------------------------|-------------------------------------|--------|
| **No payment gateway**       | Can't process card/UPI in-app       | High   |
| **No true offline mode**     | WiFi drops = dead POS               | High   |
| **No native mobile app**     | Can't install on restaurant tablet  | High   |
| **Loyalty points incomplete**| Every competitor has this           | Medium |

### HIGH (blocks upsell)

| Gap                          | Impact                              | Effort |
|------------------------------|-------------------------------------|--------|
| **No Razorpay/Stripe**       | Revenue impact: payment fees lost   | Medium |
| **No WhatsApp receipts**     | India expects this now              | Low    |
| **No SMS notifications**     | Order ready, reservation reminders  | Low    |
| **No customer-facing display** | 2nd screen for order confirmation | Medium |
| **No stock alerts via SMS**  | Manager misses low-stock at night   | Low    |

### MEDIUM (competitive parity)

| Gap                          | Impact                              |
|------------------------------|-------------------------------------|
| No Android/iOS app           | Tablet installation friction        |
| No print template editor     | Receipts look generic               |
| No franchise royalty tracking| Misses franchise billing feature    |
| No aggregator integration    | Swiggy/Zomato orders not pulled in  |

---

## India-Specific Competitors (the real threat)

These are closer competitors than Toast or Square:

| Competitor      | Strength                          | Weakness vs Rasova          |
|-----------------|-----------------------------------|-----------------------------|
| **Petpooja**    | GST, huge user base in India      | Ugly UI, no waiter call, no AI import |
| **EPOS Now**    | Global, multi-outlet              | Not India-first             |
| **GoFrugal**    | GST, inventory, manufacturing     | Enterprise pricing, complex UI |
| **Torqus**      | Fine dining, India                | Acquired by DotPe, uncertain |
| **UrbanPiper**  | Aggregator sync (Swiggy/Zomato)   | Only aggregator middleware, not full POS |
| **LimeTray**    | Aggregator + basic POS            | Limited features             |
| **Posist**      | Enterprise fine dining India      | Very expensive, slow         |

**The actual competition is Petpooja and GoFrugal** — not Toast.
Rasova's UX is already significantly better than both. That is a real advantage.

---

## What to Build Next (Priority Order Given Time Pressure)

### Must-have before first paying customer

1. **Payment gateway** (Razorpay integration) — can't do cashless without it
2. **Offline mode** — cache menu + take orders offline, sync when back
3. **WhatsApp receipt** — one API call via Twilio or Meta Cloud API

### Must-have before scale (10+ customers)

4. **Loyalty points** (complete the existing skeleton)
5. **Swiggy/Zomato pull** (via UrbanPiper or direct Dineout API)
6. **SMS alerts** (Twilio / MSG91 — 10 lines of code)

### Growth features

7. **Android PWA install prompt** — make the web app installable (it's a PWA!)
8. **Franchise royalty module**
9. **Customer-facing 2nd display**

---

## Honest Market Readiness Assessment

| Area                | Score | Notes                                          |
|---------------------|-------|------------------------------------------------|
| Feature completeness| 7/10  | Core features solid; loyalty + payments missing |
| UI/UX               | 6/10  | Before overhaul; 8/10 after                    |
| India compliance    | 9/10  | GST is strong, FSSAI fields missing            |
| Reliability         | 8/10  | All critical bugs fixed; offline mode weak     |
| Mobile experience   | 5/10  | Before overhaul; 8/10 after                    |
| Payments            | 2/10  | Biggest gap — no gateway                       |
| **Overall**         | **6/10** | **Ready for pilot; not ready for scale**    |

**You are 6-8 weeks away from being genuinely competitive with Petpooja**
if you prioritize: payments + offline + mobile UI overhaul.

---

## QSR Flow Update (from conversation)

QSR tenants do **not** use a separate token dashboard screen.
The flow is:

```
Cashier opens menu → selects items → sees current order beside menu
→ charges → prints receipt → done.

No token queue. No separate token dashboard.
Direct: Menu + Live Cart → Bill.
```

This is faster than Petpooja's QSR mode (which still has a token step).
It maps to: `billing.html` (fine dining) + `token_billing.html` (QSR)
being the same screen type — just different layouts.

For QSR, `token_dashboard.html` is hidden. The cashier lands directly on
the billing screen with the menu on the left and the running order on the right.
