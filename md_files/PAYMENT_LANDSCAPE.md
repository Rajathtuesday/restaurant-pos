# India Restaurant Payment Landscape — ELI5
**Every player, how they connect, what Rasova needs to support.**

---

## The Three Categories

```
CATEGORY 1          CATEGORY 2            CATEGORY 3
Physical Terminal   Payment Gateway       UPI-only QR
─────────────────   ───────────────       ───────────
Pine Labs           Razorpay              BharatPe
Mswipe              Cashfree              PhonePe for Business
Innoviti            PayU                  Google Pay Business
HDFC SmartHub       PayTM                 Paytm QR (standalone)
Worldline           Instamojo

Customer swipes/    Customer pays via     Customer scans static
taps card. Device   app link, QR, or      QR with any UPI app.
handles everything. net banking.          Cashier checks their
                                          phone to confirm.
```

---

## Category 1 — Physical Card Terminals (Pine Labs etc.)

```
CUSTOMER                TERMINAL                  BANK
    │                       │                       │
    │── tap/swipe card ─────►│                       │
    │                       │── auth request ───────►│
    │                       │◄── approved ───────────│
    │◄── print receipt ─────│                       │
    │                       │                       │
                     Money settles to restaurant
                     bank account in 1-2 days
```

**Pine Labs (biggest in India):**
- They GIVE restaurants the terminal hardware (rent or buy: ₹3,000-8,000)
- Supports: Visa/Mastercard swipe, chip, contactless (tap), UPI on screen
- They have an API called **Plutus** — a POS can tell Pine Labs terminal "charge ₹395"
- Terminal shows amount, customer taps → approved → Pine Labs tells POS "success"

**How restaurants use it today (without Rasova integration):**
1. Rasova shows "TOTAL: ₹395"
2. Cashier manually types ₹395 on the Pine Labs terminal
3. Customer taps card
4. Pine Labs machine prints a receipt
5. Cashier goes back to Rasova and clicks "Card" manually

**With proper integration:**
1. Rasova sends ₹395 to Pine Labs API
2. Terminal auto-shows amount
3. Customer taps
4. Pine Labs fires webhook → Rasova auto-marks paid
5. No manual step

**Complexity: HIGH.** Needs Plutus API keys, terminal serial number, bank agreement.
**Priority for Rasova: LOW.** Most small restaurants just do it manually.

---

## Category 2 — Payment Gateways (Razorpay, Cashfree etc.)

These are SOFTWARE companies. No hardware. They move money digitally.

```
CUSTOMER                RAZORPAY                RESTAURANT
    │                       │                       │
    │   sees QR on screen   │                       │
    │── scans with GPay ───►│                       │
    │── pays ₹395 ─────────►│                       │
    │                       │── webhook: PAID ──────►│ (Rasova receives this)
    │                       │                       │── auto-close order
    │                       │                       │── print bill
    │                       │                       │
                     Settlement: next day, money
                     hits restaurant bank account
                     (Razorpay takes ~2%)
```

**What Razorpay can do for Rasova:**

| Feature | What it does | Effort to build |
|---|---|---|
| UPI QR on bill | Show a QR per order. Customer scans → auto-paid | LOW — 1 week |
| Payment link | Send link via WhatsApp. Customer pays online | LOW |
| Auto-settlement | Money in bank next day, automated | FREE with account |
| Webhook | Razorpay tells Rasova "this order was paid" | LOW |
| Subscription billing | Auto-charge restaurants monthly fee | MEDIUM |
| International cards | Visa/Mastercard via Razorpay PG | LOW |
| Refunds via API | Rasova can trigger refund from Razorpay | LOW |

**Why Razorpay is the right first integration:**
- Free to create account, no monthly fee
- Test mode works without any real money
- Webhook is simple — one URL, JSON payload
- 80%+ of Indian restaurant payments are UPI — this covers them
- No hardware needed at all

**Cashfree** — same as Razorpay, slightly lower fees, better payouts API.
**PayU** — older, more enterprise, more complex.

---

## Category 3 — Standalone UPI QR (BharatPe, PhonePe Business)

```
Static QR code on counter (printed on paper or on a device)
Customer scans → pays any amount → money goes to restaurant
Cashier manually checks their BharatPe/PhonePe app to see if paid
```

**No API. No integration. Restaurant manually confirms.**

BharatPe and PhonePe Business give a static QR that accepts any amount. The cashier tells the customer "pay ₹395", customer pays, cashier checks the sound alert or their phone app.

This is what 80% of small Indian restaurants do right now. It works. It's not integrated with any POS.

**BharatPe API** — BharatPe does have an API for dynamic QR (amount pre-filled), but it's harder to get approved for (requires business documents, takes weeks).

**For Rasova:** Support this as a "UPI (Manual confirm)" option — cashier hears the sound from BharatPe, then clicks "Confirm UPI payment" in Rasova. No webhook, no auto-confirm, but tracks it in the system.

---

## What Rasova needs to support — priority order

```
Priority 1 — IMMEDIATE (before first paying customer)
  Razorpay UPI QR per order
    → Bill screen shows a scannable QR
    → Customer scans with any UPI app (GPay, PhonePe, Paytm, BHIM...)
    → Razorpay fires webhook → Rasova auto-closes order
    → No cashier manual confirmation
    Cost to build: 1 week
    Razorpay fee: ~2% per transaction

Priority 2 — AFTER first 10 customers
  Razorpay payment link
    → Send link via WhatsApp for delivery/online orders
    → Customer pays from phone
    → Same webhook auto-confirms
    Cost to build: 2-3 days (reuses Priority 1 webhook)

Priority 3 — AFTER product-market fit
  Subscription billing via Razorpay
    → Auto-charge restaurant ₹1,500/month
    → No manual invoice collection
    Cost to build: 1-2 weeks

Priority 4 — ENTERPRISE / CHAINS
  Pine Labs terminal integration
    → Rasova pushes amount to Pine Labs terminal
    → Terminal auto-shows amount for customer to tap
    → Webhook auto-confirms in Rasova
    Cost to build: 3-4 weeks + Pine Labs partnership agreement

Priority 5 — SKIP FOR NOW
  Card-not-present (online card entry)
  International payments
  EMI options
```

---

## How Razorpay UPI QR will work in Rasova

### Current flow (manual, what exists today):
```
1. Bill screen loads
2. Cashier selects "UPI" as payment method
3. Customer scans their OWN BharatPe/PhonePe QR separately
4. Cashier hears the sound, clicks "Collect Payment" in Rasova
5. Order marked paid — but Rasova has no proof UPI actually happened
```

### New flow (with Razorpay integration):
```
1. Bill screen loads
2. Rasova calls: POST api.razorpay.com/orders → { amount: 39500, currency: "INR" }
   Razorpay returns: order_id = "rzp_order_xyz"

3. Bill screen shows:
   ┌──────────────────────────────┐
   │    TOTAL: ₹ 395             │
   │                              │
   │    ██████████████████████   │ ← QR code
   │    ██████████████████████   │   (Razorpay UPI intent)
   │    ██████████████████████   │
   │                              │
   │  Scan with GPay / PhonePe   │
   │  / Paytm / BHIM / any UPI   │
   │                              │
   │  ┌──────────────────────┐   │
   │  │ Or enter UPI ID      │   │ ← fallback: type UPI ID
   │  └──────────────────────┘   │
   │                              │
   │  [Cash] [Card] [Split]       │ ← other options still there
   └──────────────────────────────┘

4. Customer scans QR with GPay
5. Customer sees: "Pay ₹395 to Spice Garden"
6. Customer taps Pay
7. GPay → NPCI → restaurant's bank → SUCCESS

8. Razorpay fires webhook to: POST https://rasova.net/payments/razorpay/webhook/
   Body: { event: "payment.captured", payload: { order_id: "rzp_order_xyz", amount: 39500 } }

9. Rasova matches order_id → finds the Rasova order → marks it paid
10. Bill screen auto-refreshes: "PAYMENT COMPLETE ✓"
11. Thermal receipt popup opens automatically
12. QSR: screen resets for next customer
```

---

## Fees breakdown (what restaurant pays)

| Method | Who charges | Fee | Example on ₹395 |
|---|---|---|---|
| Cash | Nobody | 0% | Restaurant keeps ₹395 |
| UPI via Razorpay | Razorpay | ~2% | Restaurant keeps ₹387.10 |
| Card via Pine Labs | Pine Labs + bank | 1.5-2.5% | Restaurant keeps ₹386-389 |
| BharatPe static QR | Nobody | 0% | Restaurant keeps ₹395 |

**Important:** For UPI below ₹2,000, Razorpay actually charges 0% (NPCI mandate).
Most restaurant bills are under ₹2,000. So UPI via Razorpay = FREE for the restaurant.

---

## What to implement now

1. Razorpay account → test mode keys
2. `/payments/razorpay/create-order/` — creates a Razorpay order, returns QR
3. `/payments/razorpay/webhook/` — receives payment confirmation, auto-closes Rasova order
4. Bill screen — show QR when UPI is selected, auto-confirm on webhook
5. Razorpay order ID stored on the Rasova Payment record for reconciliation
