# Rasova POS — Honest Status Report
**Last updated:** May 2026 | **Branch:** qsr

---

## What is built and working

| Area | Status | Grade |
|---|---|---|
| Multi-tenant architecture | Working | A |
| Fine-dining floor plan | Working, section grouping + urgency alerts | A- |
| QSR token system | Working | B+ |
| Kitchen display (KDS) | Working, message routing fixed | B+ |
| Thermal printing (ESC/POS) | Working — bill first, KOTs with partial cut | B+ |
| Single-printer QSR fallback | Working — KOTs route to default station if none set | B+ |
| Print preview without printer | Working — `python manage.py preview_print <id>` | A |
| Waiter notifications | Fixed — wCount bug resolved, kitchen toasts fire | B+ |
| Veg / non-veg toggle | Working in menu management | B+ |
| Waiter call system | Working | B |
| Onboarding wizard | Working — AI import, bulk tables, sample menu, <27 min | B+ |
| Setup checklist widget | Working — floating, persists until done | B |
| Zomato / Swiggy webhook | Built, untested in production | C+ |
| Offline / PWA | Partial — reads work, writes drop on disconnect | C |
| Reports / analytics | Basic, working | C+ |
| Inventory | Basic, working | C |
| Payment gateway | **Not built** | F |
| GST / e-invoice compliance | Partial — GSTIN prints on bill, no e-invoice | D |
| Error pages | Django yellow screen still shows on 500 | D |

---

## Recent work completed (May 2026)

- **Print preview command** — test printer output without hardware (`preview_print`)
- **Bill + KOT combined print** — bill prints first (full cut), KOTs follow (partial cut)
- **Single-printer QSR** — stations without a printer IP fall back to default station
- **Kitchen message routing** — waiters now only see messages for their own tables
- **Veg/non-veg toggle** — radio button in menu modal, coloured dot on each row
- **Notification poller fixed** — `wCount` undeclared bug killed all toasts silently for months
- **Floor map** — section grouping, urgency colour (>15m warn, >30m red), cooking badge, alert strip
- **Onboarding** — AI photo import in step 2, bulk table creation in step 4, sample menu button
- **Setup checklist** — floating widget shows 5/5 completion, hides when done

---

## Can you build this alone?

**The product: Yes.** You already have.
One person built what most agencies charge 20–40 lakhs for.

**The company: Not indefinitely.**
- Sales: someone has to walk into restaurants and demonstrate it
- Support: someone has to answer WhatsApp at 8pm Saturday
- Onboarding: someone has to sit with new restaurants for the first 3 days

The move is bespoke first. Charge per restaurant, do the setup yourself, support it yourself.
Learn what breaks in real service. Hire when you have 5–10 paying customers telling you
what they need.

---

## Is this project good?

Yes. Specifically:

**Genuinely strong:**
- `select_for_update()` on billing — no race condition double-payments
- `Decimal` math throughout — no float rounding on bills
- Service layer separated from views — you can change the billing logic without touching templates
- Per-outlet feature flags — right architecture for multi-tenant SaaS
- ESC/POS printing with proper cut types — works with real Epson/Star hardware
- Print preview command — support tool that most POS companies don't have internally

**Genuinely weak:**
- Printing runs in a daemon thread inside the web request — printer timeout hangs the UI
- No payment gateway — you cannot collect digital payments
- Django 500 shows a yellow stack trace to restaurant staff
- Offline writes drop silently — waiter places order during 5-second blip, it vanishes

---

## What to build next, in order

### Before the first paying customer
1. **Razorpay / Cashfree** — UPI QR on bill screen. Without this you cannot charge restaurants
   who want card or UPI payments tracked in the system.
2. **Branded error pages** — replace Django 500 with a clean screen that shows a support number.
   One yellow stack trace in front of a restaurant owner ends the trial.
3. **Celery + Redis** — printer timeouts should never hang the request. 3-hour job, 10x stability gain.

### Before scaling past 10 restaurants
4. **Offline write queue** — buffer orders locally during connectivity loss, sync on reconnect
5. **Audit trail UI** — the data exists in `OrderEvent`, just needs a page managers can open
6. **Subscription billing** — auto-collect monthly fees, pause access on non-payment
7. **E-invoice / GST return export** — needed for any restaurant filing their own returns

### Do not build yet
- AI chatbot features
- Advanced loyalty / CRM
- Multi-language
- Mobile apps (the PWA is sufficient for pilots)

---

## The competition

**Petpooja, POSist, LimeTray** — 50+ person teams, VC money, years ahead on integrations.
You cannot match them on breadth.

**Where you win:**
- Design — Rasova looks better than any of them today
- Responsiveness — you reply to WhatsApp at 9pm, they don't
- Single-printer QSR support that just works
- Custom work — chains and boutique restaurants want modifications, big players won't do it
- Tier-2 cities — restaurants paying ₹800–1,500/month to Petpooja and getting bad support

---

## Honest grade

**Product: B+.** The architecture is production-grade, the UX is better than competition,
the printing works on real hardware. The missing pieces are operational, not foundational.

**Business: C.** No paying customers yet. No payment gateway. No error pages.
The gap between "impressive codebase" and "running business" is exactly three things:
Razorpay, error pages, and one pilot restaurant willing to use it for real.

**What gets you from B+ to A: users, not features.**
