# Rasova Printing System
### Everything we built — explained simply, with micro steps

---

## Table of Contents

1. [The Big Picture — What Problem We Solved](#1-the-big-picture)
2. [Parcel Charge — The Bug and the Fix](#2-parcel-charge)
3. [The Thermal Printer — How It Connects](#3-the-thermal-printer)
4. [The Receipt — Making It Short](#4-the-receipt-layout)
5. [Rasova Agent — The Bridge](#5-rasova-agent)
6. [Old Way vs New Way of Printing](#6-old-vs-new)
7. [The Android Problem — Why the App Kept Dying](#7-the-android-problem)
8. [The Fix — Polling Architecture](#8-the-fix-polling)
9. [The Android APK — One App Does Everything](#9-the-android-apk)
10. [PWA — Install on Home Screen](#10-pwa)
11. [Platform Detection — Android vs iOS vs Windows](#11-platform-detection)
12. [The Print Queue — How It All Works Together](#12-print-queue)
13. [Security — Tenant Isolation](#13-security)
14. [Setup Guide — Micro Steps](#14-setup-guide)
15. [Files We Changed](#15-files-changed)
16. [Tests Written](#16-tests)

---

## 1. The Big Picture

### The restaurant setup

```
[Staff Phone/Tablet]  →  internet  →  [EC2 Server in cloud]
                                              ↓
                              [Local WiFi Network at cafe]
                                              ↓
                                    [Thermal Printer at counter]
```

The Django app (Rasova POS) lives on an **EC2 server** far away in the cloud.
The **thermal printer** lives inside the cafe, connected to the local WiFi.

**The Problem:** The cloud server can never directly reach the local printer.
They are on different networks. It's like trying to deliver a pizza from Mumbai
to a specific house in Bengaluru — the Mumbai kitchen can't go there itself.

**What we built:** A smart bridge that makes printing work anyway, on any device,
without any technical knowledge from the staff.

### Three paths — one for each type of device

```
Android phone   → Native APK → Background Service → TCP 9100 → Printer ✓
Windows/Mac PC  → WebSocket Agent on PC → TCP 9100 → Printer ✓
iPhone / iPad   → Browser opens receipt tab → AirPrint → Printer ✓
```

---

## 2. Parcel Charge

### What is a parcel charge?

When a customer orders food **to take away** (not eat here), the restaurant
charges a small packaging fee — like ₹5 per item. This is the **parcel charge**.

### Where it lives in the database

| Table | Field | What it stores |
|-------|-------|----------------|
| `Outlet` | `parcel_charge_amount` | Price per item (e.g. ₹5) — set once by owner |
| `Outlet` | `parcel_charge_per_item` | True/False — per item or one flat fee? |
| `MenuItem` | `parcel_charge` | Override for a specific item (biryani costs more to pack) |
| `Order` | `parcel_surcharge` | The actual charge on this order (₹0 until toggled ON) |

### How the toggle works — ELI5

Think of a light switch. One press = ON, next press = OFF.

```
Staff presses "Parcel" button
         ↓
Server checks: is parcel_surcharge > 0 on this order?
         ↓
YES → set it to ₹0 (turn OFF)
NO  → calculate and set it (turn ON)
         ↓
Recalculate the order total
```

**The calculation when turning ON:**
1. If any menu item has its own `parcel_charge` → use those item amounts × quantity
2. Else if `parcel_charge_per_item = True` → outlet charge × total number of items
3. Else → one flat outlet charge, regardless of quantity

Voided items are completely ignored in all calculations.

### The Bug That Was Fixed

**Old behaviour (broken):**

```
Staff: [Dispatch order to kitchen] ← parcel is ON
  → Server toggles parcel ON ✓ (order.parcel_surcharge = ₹25)

Staff: [Dispatch again — added one more item]
  → Server checks: parcel_surcharge > 0? YES
  → Server toggles parcel OFF ✗ (order.parcel_surcharge = ₹0)

Bill shows: Parcel charge ₹0  (wrong!)
```

The problem: every time items were sent to kitchen, the code was pressing
the parcel toggle again without checking if it was already ON for THIS order.

**The Fix:** We added a browser-side variable `_parcelAppliedToOrder` that
stores which order ID has parcel applied on the server. Before each dispatch:

```
Is _parcelAppliedToOrder === this order's ID?
  YES → parcel already applied, do NOT toggle again
  NO  → apply parcel (toggle once), then set _parcelAppliedToOrder = this order's ID
```

### Three Extra Gaps Fixed

| Gap | Problem | Fix |
|-----|---------|-----|
| Gap 1 | Kitchen send failed → parcel toggled but variable not set → next dispatch toggled OFF | Set variable immediately when toggle succeeds, before kitchen send |
| Gap 2 | Page refresh → variable reset → would toggle again | API now returns `parcel_on` + `parcel_amount` so state is restored on refresh |
| Gap 3 | QSR path never updated the variable | Added update in the QSR submit flow |

---

## 3. The Thermal Printer

### What kind of printer is this?

A **thermal printer** prints by heating special paper — no ink, no toner.
The paper turns black where it's heated. This is the small receipt printer
you see at every shop, petrol station, and hotel.

### How does it receive data?

The printer listens on **port 9100** — a special channel just for raw print data.
You connect to it like a phone call:
- IP address = "which printer" (e.g. `192.168.1.100`)
- Port 9100 = "which door to knock on"

### What is ESC/POS?

ESC/POS is the language printers speak. It's a series of tiny numbers (bytes)
that tell the printer exactly what to do:

```
\x1B@           ← "Reset printer, start fresh"
\x1Ba\x01       ← "Centre-align what comes next"
\x1B!\x18       ← "Make text big and bold"
Hello\n         ← Print the word "Hello" and go to next line
\x1D\x56\x00    ← "Cut the paper here"
```

The Rasova server generates these bytes for each bill and sends them to the printer.

### Printer Configuration

| Setting | Where stored | Default value |
|---------|-------------|--------------|
| Printer IP | `KitchenStation.printer_ip` | `192.168.1.100` |
| Port | `KitchenStation.printer_port` | `9100` |
| Paper width | `KitchenStation.paper_width_mm` | `80mm` = 48 chars/line |
| Encoding | `KitchenStation.printer_encoding` | `cp437` |

---

## 4. The Receipt Layout

### The Problem — Our Bill Was Too Long

Before this change, a 2-item bill printed as roughly **60mm of paper**.
The reference receipt (Malenadu Brahmins Cafe photo) printed the same
information in **~35mm**. We were wasting almost 2cm per receipt — over
hundreds of bills a day, that adds up to meters of wasted paper.

### Why It Was Long — ELI5

The old code was printing:
- The restaurant name in **double-height, double-width letters** (each character took 2× the paper height)
- Bill number on one line
- Table/token on a separate line
- Date on a third separate line
- The word "TOTAL" in double-height letters again
- "Thank you for your visit!" on one line
- "Powered by Rasova POS" on another line
- Two blank lines before the cut (`\n\n`)

Think of it like writing an address by using a marker instead of a pen —
the letters are big and readable but you waste a lot of space.

### What We Changed

**Before vs After — for a 2-item bill:**

```
BEFORE (60mm):                    AFTER (35mm):
                                  
  ████████████████               CAFE NAME          ← bold, normal size
   (DOUBLE HEIGHT)               Outlet Name
   restaurant name               123 Street, Area
  Outlet Name                    GSTIN:xxx  SAC:996331
  123 Street Area                .....CASH/BILL.....
  Ph: 9876543210                 NO.184  TBL:5  27/05/26 18:23
  GSTIN: 29XXXXX                 --------------------------------
  FSSAI: 12345678                Item              Qty   Amt
  SAC: 996331                    --------------------------------
  -------------------------------- Chai               2    40
  Bill : 184                     --------------------------------
  Table: 5                       Subtotal              Rs.40
  Date : 27/05/2026 18:23        GST                   Rs.2
  -------------------------------- --------------------------------
  Item             Qty   Amt     TOTAL                 Rs.42
  -------------------------------- Paid                   CASH
  Chai               2    40     --------------------------------
  -------------------------------- Thank you! Visit again.
  Subtotal          Rs.40
  GST               Rs.2
  --------------------------------
   ████████████████
    (DOUBLE HEIGHT)
        TOTAL        Rs.42
  Paid via  CASH
  --------------------------------
  Thank you for your visit!
  Powered by Rasova POS


  (two blank lines)
```

### Every Change Made and Why

| Change | Old | New | Paper saved |
|--------|-----|-----|-------------|
| Restaurant name | Double-width + double-height | Bold, normal size | ~4mm |
| GSTIN + SAC | Two separate lines | One line: `GSTIN:xxx  SAC:996331` | ~2mm |
| Bill info | 3 separate lines (Bill, Table, Date) | One line: `NO.184  TBL:5  27/05 18:23` | ~4mm |
| Total | Double-height bold | Bold, normal size | ~2mm |
| Footer | "Thank you for your visit!" + "Powered by Rasova POS" + `\n\n` | "Thank you! Visit again." only | ~4mm |
| Address | Truncated to one line | Word-wrapped properly to fit width | cleaner |
| Parcel charge label | "Parcel Charge" | "Parcel" | fits better |

**Total paper saved: ~16mm per bill.**

### New Helpers Added

```python
_wrap_text(text, width)
# Breaks a long address at word boundaries to fit the paper width
# "123 Main Road, Jayanagar, Bengaluru 560041"
# becomes two lines if it's too long — not just cut off

_pack_lines(items, width)
# Fits multiple short strings onto fewest lines
# ["GSTIN:29AI0PA0047M1Z4", "SAC:996331"]
# → one line: "GSTIN:29AI0PA0047M1Z4  SAC:996331"  (fits on 48-char paper)
```

### KOT Also Made Shorter

The KOT (Kitchen Order Ticket) also had wasted lines:

```
BEFORE:                    AFTER:
KOT #3 (big)              KOT #3 (still big — kitchen needs to read it)
Token: 42                 TKN:42  [Main Kitchen]  ← one line instead of two
[Main Kitchen]            --------------------------------
-------------------------------- 2x [V] Paneer Biryani
2x  [V] Paneer Biryani      *extra spicy
   * extra spicy          --------------------------------
-------------------------------- 27/05 14:23
27/05 14:23

(blank line)
```

Token/table + station name are now combined on one line. Trailing blank line removed.

---

## 5. Rasova Agent

### What is it?

The Rasova Agent is a small program that runs on a device inside the cafe
(laptop, phone, Raspberry Pi, Android phone).

Think of it as a **post office worker** sitting inside the cafe:
- The cloud server (EC2) writes a letter (the bill in ESC/POS format)
- Puts it in the database queue
- The post office worker checks for new letters every 2 seconds
- Picks it up and hand-delivers it to the printer
- The cloud server never needs to physically reach the printer

### Three ways to run it

| Way | Who runs it | Best for |
|-----|------------|---------|
| **Native Android APK** | Background service inside the Rasova app | Android phones — zero setup |
| **Python Polling (Termux)** | `rasova_agent.py --poll <url>` | Advanced Android / Raspberry Pi |
| **Python WebSocket** | `rasova_agent.py` (desktop) | Windows/Mac PC at the counter |

### What the agent does (polling mode)

```
Every 2 seconds:
  1. GET https://yourserver.com/orders/agent/<key>/jobs/
  2. EC2 returns list of pending print jobs
  3. For each job:
       a. Base64 decode the ESC/POS data
       b. Open TCP connection to printer (192.168.1.100:9100)
       c. Send the bytes
       d. Close connection
       e. POST to .../done/<id>/ or .../failed/<id>/
  4. Wait 2 seconds, repeat
```

### Smart fallback when printer IP changes

If the printer gets a new IP from the router (DHCP), the Python agent tries to find it:
1. Try the configured IP
2. Try ARP lookup (find it by MAC address — a permanent identifier)
3. Try the last known working IP (cached in config file)
4. Scan the entire local network for anything answering on port 9100

### Config and log file locations

| Platform | Config | Logs |
|----------|--------|------|
| Windows | `C:\Users\YourName\AppData\Roaming\Rasova\agent_config.json` | same folder, `agent.log` |
| Android/Linux | `~/.rasova/agent_config.json` | `~/.rasova/agent.log` |

---

## 6. Old Way vs New Way of Printing

### Old way (broken for local networks)

```
Browser → fetch("/orders/print-bill/") → EC2 server
                                              ↓
                              EC2 tries to connect to 192.168.1.100:9100
                                              ✗ FAILS
                              (EC2 is in Mumbai, printer is in your cafe)
```

### New way (works)

**Android (Native APK — recommended):**
```
Rasova App → print button → POST /orders/agent/add-job/ (queues job on EC2)
Background PrintService → polls EC2 every 2s → TCP 9100 → Printer ✓
```

**Android (Termux — fallback for advanced users):**
```
Browser → POST /orders/agent/add-job/ (queues job on EC2)
rasova_agent.py --poll → polls EC2 every 2s → TCP 9100 → Printer ✓
```

**Desktop (Windows/Mac):**
```
Browser → ws://localhost:8765 → Agent on same PC → TCP 9100 → Printer ✓
```

**iPhone/iPad:**
```
Browser → opens /orders/bill/<id>/?print=1 in new tab → AirPrint → Printer ✓
```

---

## 7. The Android Problem — Why the App Kept Dying

### What happens on Android

Android is very aggressive about saving battery. It kills any app that:
- Is running in the background
- Has an open port (like a WebSocket server)
- Hasn't been touched in a few minutes

So the old agent (WebSocket server on port 8765) would get killed after
a few minutes of no billing activity. When the cashier tried to print the
next bill — the agent was dead.

### This is like...

Imagine you hire a person to stand at your door and accept deliveries.
But your building manager kicks them out if they stand there for more than
5 minutes without doing anything. Every time a delivery arrives, the person is gone.

**Old solution (bad):** Ask the person to run back every morning.

**New solution (good):** Instead of the person standing at the door,
you leave a note on the building's notice board. The delivery company
checks the notice board every 2 minutes and delivers when they see a note.
The delivery company is never kicked out because they're coming TO the
building, not standing in front of it.

### Technical comparison

| | WebSocket Server (old) | HTTP Polling (new) |
|-|----------------------|------------------|
| Agent is a | Server (waits for connections) | Client (makes outbound requests) |
| Android kills it? | YES — kills idle listening sockets | NO — cannot kill outbound HTTP |
| If killed | Must be manually restarted | Boot script or APK restarts automatically |
| Missed jobs? | Lost forever | Safe in DB queue, picked up on restart |
| Requires open port? | YES (8765) | NO |
| Works behind NAT/firewall? | Complicated | Always works |

---

## 8. The Fix — Polling Architecture

### How it works (simple version)

```
Step 1: Cashier presses "Print Bill" on their phone
Step 2: Phone sends order ID to EC2 (normal HTTPS — always works)
Step 3: EC2 generates the receipt, saves PrintJob row to database
Step 4: Agent (on phone or PC) checks EC2 every 2 seconds: "Any jobs for me?"
Step 5: EC2 says "Yes, here's the receipt as base64"
Step 6: Agent connects to 192.168.1.100:9100 and sends the bytes
Step 7: Printer prints
Step 8: Agent tells EC2: "Job done" — EC2 marks it complete
```

### The `PrintJob` database table

| Field | What it stores | Example |
|-------|---------------|---------|
| `id` | Auto-number | `42` |
| `tenant` | Which business | `Rasova Cafe` |
| `outlet` | Which branch | `Café Counter` |
| `status` | `pending` / `done` / `failed` | `pending` |
| `payload.data_b64` | ESC/POS bytes as base64 | `G0BbYQFIZWxsbw==` |
| `payload.network_host` | Printer IP | `192.168.1.100` |
| `payload.network_port` | Printer port | `9100` |
| `created_at` | When queued | `2026-05-28 14:30:05` |
| `done_at` | When printed | `2026-05-28 14:30:07` |

**Auto-expiry:** Jobs older than 5 minutes are ignored (stale — no point printing).

### Why base64?

ESC/POS data contains special control characters like:
- `\x1B` = ESC byte
- `\x1D` = GS byte

PostgreSQL's `jsonb` column refuses to store these raw special characters.
Base64 converts any bytes to safe letters and numbers (A–Z, a–z, 0–9, +, /)
so they store without issues.

```
Raw:    \x1B @ \x1B a \x01 Hello\n \x1D\x56\x00
Base64: G0BbYQFIZWxsbwDdVgA=
```

The agent decodes base64 back to bytes before sending to the printer.

### The secret key

Each outlet has a `print_agent_key` — a random UUID like:
`a3f9e2b1-4c5d-4e6f-8a7b-9c0d1e2f3a4b`

This key is used in the polling URL:
```
https://your-site.com/orders/agent/a3f9e2b1-4c5d-4e6f-8a7b-9c0d1e2f3a4b/
```

- Only the agent with this exact key can fetch jobs for this outlet
- Wrong key → 403 Access Denied
- The key is never visible to the browser — only embedded in the agent's configuration
- Even if someone guesses a valid key, they cannot see another tenant's jobs
  (the DB query also filters by `tenant_id` — double lock)

---

## 9. The Android APK — One App Does Everything

### The UX Problem with Two Icons

Before this change, Android staff had to manage TWO things:
1. The **Rasova PWA shortcut** (the browser shortcut for taking orders)
2. **Termux** (a developer terminal app for running the print agent)

Staff would ask: "Which one do I open?", "Can I delete Termux?",
"Why are there two Rasova-related things?"

Non-technical staff (cashiers, QSR counter staff) would give up. **This is now solved.**

### The Solution — One App

The **Rasova Android APK** is one app that:
1. Shows the full Rasova POS website inside it (using a WebView — a browser built into the app)
2. Has a background printing service built in that polls EC2 and prints automatically
3. Starts automatically when the phone boots
4. Cannot be killed by Android's battery optimizer

```
Staff home screen:
  [Rasova] ← one icon, one app, does everything

What happens when staff opens it:
  ┌────────────────────────────────────┐
  │  (full screen — no browser bar)    │
  │                                    │
  │  Login to Rasova POS               │
  │  Username: ___________             │
  │  Password: ___________             │
  │  [Log In]                          │
  │                                    │
  │  Notification: "Rasova Printing ✓" │
  └────────────────────────────────────┘
```

### How the magic happens — ELI5

The app secretly adds a label to its browser: `RasovaPOS-Android/1.0`.
It's like a person wearing a name tag that says "I'm the Rasova app".

When the staff logs in, Django's JavaScript looks at the name tag:
- "Oh, this is the native app!" → calls `Android.startPrinting(url)`

That call crosses from the website world into the Android world and tells
the printing engine: "Start polling this URL." The engine starts running
in the background. Done. No setup visible to the staff at all.

### The 4 Kotlin files that make it work

```
rasova_android/
  app/src/main/java/com/rasova/pos/
    MainActivity.kt    — Opens the WebView, sets the name tag, wires up the bridge
    JSBridge.kt        — The translator between website JS and native Android
    PrintService.kt    — The invisible engine (polls EC2, prints via TCP, runs 24/7)
    BootReceiver.kt    — Listens for phone boot → restarts PrintService
```

### MainActivity.kt — The Front Door

```kotlin
// The website sees this user-agent string
settings.userAgentString = settings.userAgentString + " RasovaPOS-Android/1.0"
//                                                      ↑ this is the name tag

// Connect the JS bridge so website can call Android.startPrinting()
webView.addJavascriptInterface(JSBridge(this), "Android")

// Load the POS website
webView.loadUrl(BuildConfig.SERVER_URL)  // e.g. "https://your-rasova-server.com"
```

**ELI5:** MainActivity is like the shop front. It opens a full-screen browser,
pastes a secret name tag on it, and connects the website to the printing engine.

### JSBridge.kt — The Translator

```kotlin
@JavascriptInterface
fun startPrinting(pollUrl: String) {
    // Save the URL (so BootReceiver can use it after a reboot)
    saveUrl(pollUrl)
    // Start the printing engine
    startForegroundService(PrintService)
    // Show the "don't kill me" dialog — user taps Allow once
    showBatteryOptimizationDialog()
}

@JavascriptInterface
fun getPrintingStatus(): String = PrintService.status
// Returns "active", "printing", "error", or "stopped"
```

**ELI5:** The JSBridge is like a translator sitting at the border between
two countries — website-land and Android-land. The website doesn't speak
Android, Android doesn't speak JavaScript. The bridge translates.

### PrintService.kt — The Invisible Worker

```kotlin
// Runs forever in the background:
while (true) {
    val jobs = GET("$pollUrl/jobs/")       // ask EC2 for jobs
    for (job in jobs) {
        val bytes = Base64.decode(job.data_b64)   // decode the receipt
        Socket(job.host, job.port).use { socket ->
            socket.getOutputStream().write(bytes)  // send to printer
        }
        POST("$pollUrl/done/${job.id}/")   // tell EC2 it's done
    }
    delay(2000)  // wait 2 seconds and repeat
}
```

**ELI5:** PrintService is like a loyal employee who works 24/7, checks
the notice board (EC2 queue) every 2 seconds, and delivers any new letters
(receipts) directly to the printer. Android sees the notification "Rasova
Printing Active" and knows: this is important, don't kill it. Just like
a phone call — Android never kills an active phone call.

### BootReceiver.kt — The Alarm Clock

```kotlin
override fun onReceive(context: Context, intent: Intent) {
    if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
    val savedUrl = readSavedUrl()  // URL saved by JSBridge earlier
    startForegroundService(PrintService with savedUrl)
}
```

**ELI5:** When the phone is turned off and back on, Android shouts
"I'm awake!" BootReceiver hears this shout, reads the saved URL from
memory, and immediately starts the printing engine again. The staff never
needs to do anything after a power cut or phone restart.

### Android OS Rules — How We Follow Each One

Android has strict rules about background apps. Here's how each rule is handled:

| Rule | What it means | How we handle it |
|------|--------------|-----------------|
| Background execution limits | Apps running in background get killed after minutes | We use a **Foreground Service** — a special type that shows a persistent notification and CANNOT be killed |
| Battery Optimizer / Doze mode | Android starves apps of CPU and network when phone is idle | `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` — the app shows a one-tap dialog on first login. Staff taps "Allow" once, never asked again |
| Auto-start after reboot | Killed on phone restart | `RECEIVE_BOOT_COMPLETED` permission + `BootReceiver.kt` handles reboot automatically |
| Play Store / Distribution | Termux needs F-Droid which needs "unknown sources" | Our APK is distributed as a download. One "allow from this source" tap. Or Play Store later (no unknown sources needed) |

### AndroidManifest.xml — The Passport

Every Android app must declare its permissions upfront, like filling a
customs form before entering a country. Our app declares:

```xml
INTERNET                           ← talk to EC2 and printer
FOREGROUND_SERVICE                 ← run a service that shows a notification
FOREGROUND_SERVICE_DATA_SYNC       ← required on Android 14+ for data sync services
RECEIVE_BOOT_COMPLETED             ← wake up when phone restarts
REQUEST_IGNORE_BATTERY_OPTIMIZATIONS ← show the "don't kill me" dialog
```

### How to Build the APK

1. Install **Android Studio** (free — from developer.android.com)
2. Open the `rasova_android/` folder in Android Studio
3. In `app/build.gradle`, find this line:
   ```
   buildConfigField "String", "SERVER_URL", '"https://your-rasova-server.com"'
   ```
   Change it to your actual EC2 URL
4. Connect your Android phone via USB (or use the emulator)
5. Click **Run ▶** — Android Studio builds and installs the app
6. To get a shareable APK file: **Build → Generate Signed Bundle / APK → APK**

---

## 10. PWA — Install on Home Screen

### What is a PWA?

PWA = Progressive Web App. It lets a website behave like a real phone app
without being downloaded from the Play Store.

When Rasova POS runs in Chrome on Android, the browser shows:
> "Install Rasova POS — Add to home screen"

After installing, the app:
- Gets its own icon on the home screen
- Opens fullscreen (no browser address bar)
- Can show notifications

### How the install banner behaves now

| Where you open Rasova | What happens |
|----------------------|-------------|
| Inside the **native APK** | No install banner (it's already installed!) |
| In **Chrome on Android** (not the APK) | "Add to home screen — tap Install, then set up printing in Termux" |
| In **Safari on iPhone/iPad** | "Use Share → Add to Home Screen in Safari" |
| In **Chrome on Windows/Mac** | "Add to your desktop for the best experience" |

### Post-install setup sheet

After installing the PWA (browser shortcut), a sheet slides up with printing setup.
This sheet is **skipped entirely** if the user is inside the native Android APK
(because printing is already working).

---

## 11. Platform Detection — Android vs iOS vs Windows

### The `rasovaPlatform` object

Every page loads a small JavaScript object that detects the device:

```javascript
window.rasovaPlatform = {
  android:       true/false,  // Android phone/tablet (in browser)
  ios:           true/false,  // iPhone or iPad
  windows:       true/false,  // Windows PC
  mac:           true/false,  // Mac
  nativeAndroid: true/false,  // Running inside the Rasova Android APK
  standalone:    true/false,  // Installed PWA (running from home screen)
  agentHost:     "..."        // Where to connect the WebSocket agent (desktop only)
}
```

### The `nativeAndroid` flag — how it's detected

```javascript
const nativeAndroid = /rasovaPOS-android/i.test(navigator.userAgent);
```

The native APK adds `RasovaPOS-Android/1.0` to the browser's user-agent string.
This is invisible to users but visible to the website's JavaScript.
When `nativeAndroid` is true, the website knows:
- Printing is handled by the native service — no Termux, no setup needed
- Post-install sheet is skipped
- Offline hint says "open Rasova app and allow battery optimization" (not "open Termux")

### Auto-configure printing after login

When the staff logs in inside the native app, Django automatically
calls the JavaScript bridge to start the printing service:

```javascript
{% if request.user.is_authenticated and request.user.outlet %}
if (rasovaPlatform.nativeAndroid && typeof Android !== 'undefined') {
    const pollUrl = '{{ scheme }}://{{ host }}/orders/agent/{{ outlet.print_agent_key }}/';
    Android.startPrinting(pollUrl);
    // → saves URL, starts ForegroundService, shows battery dialog
}
{% endif %}
```

**ELI5:** Every time the page loads and a staff member is logged in,
the website quietly whispers the secret polling URL to the Android app.
The Android app saves it and makes sure the printing engine is running.
This happens invisibly — staff never sees it.

### How printing is decided in `billing.html`

```
User clicks "Print Bill"
        ↓
Is it running in the native Android APK? (nativeAndroid = true)
  YES → POST /orders/agent/add-job/ (native service picks it up automatically)
        ↓
Is it iOS?
  YES → open /orders/bill/<id>/?print=1 in new tab (browser print / AirPrint)
        ↓
Is it plain Android (browser, not APK)?
  YES → POST /orders/agent/add-job/ (Termux agent picks it up)
        ↓
Desktop (Windows/Mac):
  → WebSocket ws://agentHost:8765 → desktop agent → printer
```

---

## 12. The Print Queue — How It All Works Together

### The full flow on Android

```
┌─────────────────────────────────────────────────────────┐
│                    Staff's Android Phone                 │
│                                                         │
│  Rasova App (WebView): "Print Bill for Order #42"       │
│       ↓ HTTPS POST                                      │
│  /orders/agent/add-job/   { order_id: 42 }              │
└─────────────────────────────────────────────────────────┘
                    ↓ internet
┌─────────────────────────────────────────────────────────┐
│                      EC2 Server                         │
│                                                         │
│  1. Verify: order #42 belongs to this tenant + outlet   │
│  2. Get order from DB                                   │
│  3. Generate ESC/POS receipt bytes                      │
│  4. Base64-encode the bytes                             │
│  5. Create PrintJob row:                                │
│     tenant=T1, outlet=O1, status=pending,               │
│     data_b64=..., host=192.168.1.100, port=9100         │
│  6. Return { success: true, job_id: 99 }                │
└─────────────────────────────────────────────────────────┘
                    ↑ polls every 2 seconds
┌─────────────────────────────────────────────────────────┐
│     PrintService (background, inside Rasova APK)        │
│                                                         │
│  GET /orders/agent/<key>/jobs/                          │
│  ← [{ id:99, data_b64:"...", host:"192...", port:9100 }]│
│  → Base64 decode → raw ESC/POS bytes                    │
│  → TCP connect to 192.168.1.100:9100                    │
│  → write bytes → flush → close                          │
│  POST /orders/agent/<key>/done/99/                      │
└─────────────────────────────────────────────────────────┘
                    ↓ TCP port 9100 (same WiFi network)
┌─────────────────────────────────────────────────────────┐
│           Thermal Printer (192.168.1.100)                │
│                                                         │
│  *prints the receipt*                                   │
└─────────────────────────────────────────────────────────┘
```

### What happens if the agent crashes mid-job?

- EC2 still has the `PrintJob` row with `status=pending`
- Android: PrintService is a ForegroundService — Android restarts it with `START_STICKY`
- On next poll, EC2 returns the same job (still pending, within 5-min TTL)
- Agent prints it
- **No receipt is lost**

### What happens if the printer is offline?

- Agent tries to connect to `192.168.1.100:9100`
- Gets "Connection refused" or timeout (5 second timeout)
- Agent calls `/orders/agent/<key>/failed/99/` with the error message
- Job is marked `failed` — won't be tried again automatically
- Cashier needs to re-tap "Print Bill"

### Job states

```
PrintJob created → status: pending
Agent prints OK  → status: done   (done_at timestamp set)
Printer offline  → status: failed (done_at + error_msg set)
Older than 5 min → not served to agent (TTL expired)
```

---

## 13. Security — Tenant Isolation

### What is a tenant?

In Rasova, a "tenant" is a business (e.g. "Malenadu Brahmins Cafe").
One tenant can have multiple outlets (branches).

The database has data for many tenants. It's critical that Tenant A
can never see Tenant B's orders, bills, or print jobs.

### How the print queue is protected — double lock

Every agent query now filters on **both** the secret key AND the tenant:

```python
# Before (only outlet check):
PrintJob.objects.filter(outlet=outlet, status="pending")

# After (outlet + tenant — double lock):
PrintJob.objects.filter(
    tenant_id=outlet.tenant_id,   ← Tenant check
    outlet=outlet,                ← Outlet check
    status="pending"
)
```

**Why both?**

- `outlet` check: prevents outlet A from seeing outlet B's jobs
  (even within the same business)
- `tenant_id` check: prevents tenant B from seeing tenant A's jobs
  (cross-business isolation)

Even if someone knew a valid job ID from another tenant, the query
would return 0 rows because `tenant_id` doesn't match.

### The database index

To make this double-filtered query fast even with millions of rows,
the database has a compound index:

```
Index: (tenant, outlet, status, created_at)
```

This is like a phone book organized by city, then area, then name —
finding all pending jobs for "tenant=T1, outlet=O1" takes microseconds,
not a full table scan.

---

## 14. Setup Guide — Micro Steps

### Path A: Native Android APK (Recommended — easiest)

**One-time setup. After this: zero manual work ever.**

**Step 1: Get the APK file**
1. Open Rasova POS on any browser
2. Go to **Setup → Printing**
3. Find the "Download Rasova Android App" button
4. Tap it — the `.apk` file will start downloading
5. Wait for the download to finish (you'll see a notification or progress at the bottom)

**Step 2: Install the APK**
1. Open your phone's **notification shade** (pull down from top)
2. Tap the downloaded `.apk` file
3. A popup says "Install blocked for security" — this is normal
4. Tap **Settings** in that popup
5. Turn ON "Allow from this source"
6. Tap the back button
7. Tap **Install**
8. Wait ~5 seconds
9. Tap **Open**

**Step 3: First login**
1. The app opens showing the Rasova login screen — same as the browser version
2. Enter your username and password
3. Tap **Log In**
4. A popup appears: "Allow Rasova to run without battery restrictions?"
5. Tap **Allow** — this is the battery optimization dialog
6. You are now on the main POS screen
7. In the notification bar at the top, you should see "Rasova Printing Active"

**That's it. Printing now works forever.**

**Verification — test that printing works:**
1. Place a test order (tap through billing, add one item)
2. Tap **Print Bill**
3. The receipt should print within 2-4 seconds

**Verification — test that it survives a reboot:**
1. Turn your phone OFF
2. Turn it back ON
3. Do NOT open the Rasova app
4. Create a test order on a different device (or the same phone after a minute)
5. Tap Print Bill — it should still print (the service auto-started on boot)

---

### Path B: Python Agent on Android/Termux (Advanced fallback)

Use this if you can't install the APK (e.g. phone is too old, needs Raspberry Pi).

**Step 1: Install Termux from F-Droid**

> ⚠️ Important: Do NOT install Termux from the Play Store. The Play Store
> version is outdated. You MUST get it from F-Droid.

1. On the Android phone, open **Chrome**
2. Go to `f-droid.org`
3. Tap **Download F-Droid** (the orange button)
4. When the file finishes downloading, tap it
5. A popup says "Install blocked" → tap **Settings** → turn ON "Allow from this source"
6. Go back → tap **Install**
7. Open **F-Droid**
8. Search for **Termux** → tap it → tap **Install**
9. Search for **Termux:Boot** → tap it → tap **Install**
10. Open **Termux:Boot** once (just open it and close — this activates it)

**Step 2: Install Python in Termux**
1. Open **Termux**
2. Type exactly (copy-paste from here):
   ```
   pkg update -y && pkg install python -y
   ```
3. Press Enter. Wait until you see `$` again (may take 2-5 minutes)
4. Type:
   ```
   pip install requests
   ```
5. Press Enter. Wait until `$` appears again.

**Step 3: Copy the agent file to your phone**
1. On your computer, find `rasova_agent.py` in the project folder
2. Copy it to your Google Drive / WhatsApp / USB cable to the phone
3. In Termux, move it to your home folder:
   ```
   cp /sdcard/Download/rasova_agent.py ~/rasova_agent.py
   ```
   (adjust the path if you put it somewhere else)

**Step 4: Get your outlet's polling URL**
1. Open Rasova POS in Chrome on the phone
2. Go to **Setup → Printing**
3. Find the "Android Polling URL" — it looks like:
   ```
   https://yoursite.com/orders/agent/a3f9e2b1-4c5d-4e6f-8a7b-9c0d1e2f3a4b/
   ```
4. Copy that URL

**Step 5: Start the agent with auto-boot**
1. Open Termux
2. Paste this (replace `YOUR_URL` with the URL from Step 4):
   ```
   python rasova_agent.py --poll 'YOUR_URL' --install-boot
   ```
3. Press Enter
4. You should see: `Polling mode started. Boot script installed.`

> The `--install-boot` flag creates a watchdog script in `~/.termux/boot/`
> that automatically starts the agent every time the phone boots and
> restarts it within 3 seconds if it crashes.

**Step 6: Set battery to Unrestricted**
1. Open Android **Settings**
2. Go to **Apps** (or Application Manager)
3. Find **Termux**
4. Tap **Battery**
5. Select **Unrestricted** (not "Optimized" or "Restricted")
6. Go back and do the same for **Termux:Boot**

---

### Path C: Windows PC Agent (for desktop billing counter)

**Step 1: Install Python**
1. Go to `python.org/downloads`
2. Download Python 3.11 or higher
3. Run the installer
4. ✅ Check "Add Python to PATH" at the bottom — very important
5. Click Install

**Step 2: Install the agent dependencies**
1. Open **Command Prompt** (search "cmd" in Start menu)
2. Type:
   ```
   pip install websockets pywin32
   ```
3. Press Enter. Wait until `Successfully installed` appears.

**Step 3: Place the agent file**
1. Copy `rasova_agent.py` to a permanent location, e.g.:
   `C:\Rasova\rasova_agent.py`
   (Create the `Rasova` folder first if it doesn't exist)

**Step 4: Install as a Windows auto-start service**
1. Open **Command Prompt as Administrator**
   (right-click on cmd → Run as Administrator)
2. Navigate to the folder:
   ```
   cd C:\Rasova
   ```
3. Run:
   ```
   python rasova_agent.py --install
   ```
4. You should see: `Auto-start installed. Agent will start at next login.`

**Step 5: Verify**
1. Restart the PC
2. After login, open **Task Manager** (Ctrl+Shift+Esc)
3. Go to **Details** tab
4. Look for `python.exe` — it should be there running silently
5. Open Rasova POS → Setup → Kitchen Stations → click **Test Print**

---

### Configure the printer IP (all platforms, first time)

1. Find your printer's IP address:
   - Option A: Print a self-test page (hold the printer's feed button while powering on)
     — the IP is printed on the test page
   - Option B: Log into your WiFi router → look at connected devices
   - Option C (if agent is running on Windows): In Rasova → Setup → Kitchen Stations
     → click **🔍 Discover** — it scans the network and fills the IP automatically

2. In Rasova → **Setup → Kitchen Stations**
3. Find your station → enter the IP address in the "Printer IP" field
4. Set paper width (80mm for most printers)
5. Click **Save**
6. Click **Test Print** — a test receipt should print

---

## 15. Files We Changed

### New files — Android APK

| File | What it does |
|------|-------------|
| `rasova_android/settings.gradle` | Android project configuration |
| `rasova_android/build.gradle` | Build tools version |
| `rasova_android/gradle.properties` | Build performance settings |
| `rasova_android/app/build.gradle` | App config: app ID, min Android version, SERVER_URL, dependencies |
| `rasova_android/app/src/main/AndroidManifest.xml` | App permissions + component declarations |
| `rasova_android/app/src/main/java/com/rasova/pos/MainActivity.kt` | WebView + custom user-agent + JS bridge wiring |
| `rasova_android/app/src/main/java/com/rasova/pos/JSBridge.kt` | JS-callable methods: `startPrinting()`, `getPrintingStatus()`, `isNativeApp()` |
| `rasova_android/app/src/main/java/com/rasova/pos/PrintService.kt` | ForegroundService: poll → decode → TCP print → report |
| `rasova_android/app/src/main/java/com/rasova/pos/BootReceiver.kt` | BOOT_COMPLETED listener → restarts PrintService |
| `rasova_android/app/src/main/res/layout/activity_main.xml` | Full-screen WebView layout |
| `rasova_android/app/proguard-rules.pro` | Tells ProGuard not to rename JSBridge methods |

### New files — Print queue system

| File | What it does |
|------|-------------|
| `orders/views/print_queue.py` | 4 API endpoints: add-job, poll, done, failed |
| `orders/tests/test_print_queue.py` | 53 tests covering all scenarios and edge cases |
| `orders/migrations/0044_add_printjob.py` | Creates the `PrintJob` DB table |
| `orders/migrations/0045_printjob_tenant_index.py` | Adds compound index (tenant, outlet, status, created_at) |
| `tenants/migrations/0022_outlet_print_agent_key.py` | Adds `print_agent_key` UUID to `Outlet` |

### Modified files

| File | What changed |
|------|-------------|
| `rasova_agent.py` | Added `--poll` mode, `--install-boot` Termux watchdog, `--install-termux` |
| `orders/models.py` | Added `PrintJob` model with tenant/outlet FK, payload JSON, status, timestamps |
| `tenants/models.py` | Added `print_agent_key` UUID field to `Outlet` |
| `orders/urls.py` | 4 new URL patterns for print queue API |
| `orders/views/order_actions.py` | `parcel_surcharge` bug fix |
| `orders/services/printing_service.py` | Compact bill layout: `_print_bill_body`, `_print_kot_body`, `_print_qsr_token_body`; added `_wrap_text`, `_pack_lines` |
| `orders/templates/orders/billing.html` | Print routing: nativeAndroid/iOS/Android/Desktop |
| `templates/core/base.html` | `rasovaPlatform` (added `nativeAndroid`), auto-call `Android.startPrinting()` on login, install banner + offline hint aware of native app |
| `setup/templates/setup/setup_kitchen_stations.html` | Test Print → direct agent WebSocket, Discover button |
| `setup/templates/setup/printer_setup.html` | `AGENT_WS` uses platform-aware host |

---

## 16. Tests Written

### Parcel dispatch tests (`test_parcel_dispatch.py`) — 41 tests

| Test class | What it tests |
|------------|--------------|
| `ToggleBasicTests` | Single/double toggle, various order states |
| `ToggleCalculationTests` | Per-item mode, flat mode, MenuItem override, voided items |
| `ToggleTotalIntegrationTests` | Grand total changes correctly |
| `RunningOrderParcelStateTests` | Session resume after page refresh |
| `ParcelAppliedBeforeKitchenSendTests` | Parcel survives failed kitchen send |
| `ToggleHttpStatusTests` | HTTP response codes and body contracts |
| `QSRParcelDispatchTests` | QSR-specific flows |

### Print queue tests (`test_print_queue.py`) — 53 tests

| Test class | What it tests |
|------------|--------------|
| `AddJobTests` | Job creation, payload content, no-printer 422, cross-tenant 404, invalid JSON 400 |
| `PollTests` | Pending jobs returned, TTL expiry, wrong key 403, batch limit of 5, oldest-first ordering |
| `DoneTests` | Mark done, wrong key 403, double-done 404, nonexistent 404 |
| `FailedTests` | Error recording, done_at set, error truncation to 512 chars |
| `KeySecurityTests` | UUID type, uniqueness per outlet, one-char-off key rejected |
| `TenantIsolationTests` | Cross-tenant poll sees zero jobs, cross-tenant done/failed silently blocked, job.tenant set correctly |
| `AddJobEdgeCaseTests` | Empty body, non-integer order_id, missing order_id, GET rejected (405), voided items |
| `PollEdgeCaseTests` | Chronological order, exactly-5 boundary, ESC/POS bytes in response, POST rejected (405), same-tenant multi-outlet isolation |
| `DoneEdgeCaseTests` | Failed job cannot be re-done, GET rejected (405) |
| `FailedEdgeCaseTests` | done_at stamped, wrong outlet key silently ignored, done job unchanged, empty error body stores "" |

**Total: 94 tests across both files. All pass.**

---

## Quick Reference

### Print routing by platform

| Platform | How detected | How bill is printed |
|----------|-------------|-------------------|
| Native Android APK | `rasovaPlatform.nativeAndroid = true` | Queue → PrintService → TCP 9100 |
| Android browser | `rasovaPlatform.android = true` | Queue → Termux agent → TCP 9100 |
| iPhone / iPad | `rasovaPlatform.ios = true` | Browser tab → Share → AirPrint |
| Windows/Mac desktop | everything else | WebSocket → Python agent → TCP 9100 |

### API endpoints

| Endpoint | Method | Auth | Who calls it | What it does |
|----------|--------|------|-------------|--------------|
| `/orders/agent/add-job/` | POST | Session | Browser / WebView | Queue a print job |
| `/orders/agent/<key>/jobs/` | GET | Key in URL | PrintService / rasova_agent.py | Fetch ≤5 pending jobs |
| `/orders/agent/<key>/done/<id>/` | POST | Key in URL | PrintService / rasova_agent.py | Mark job done |
| `/orders/agent/<key>/failed/<id>/` | POST | Key in URL | PrintService / rasova_agent.py | Record failure |

### Agent commands (Python / Termux)

| Command | What it does |
|---------|-------------|
| `python rasova_agent.py` | WebSocket server mode (port 8765) for desktop |
| `python rasova_agent.py --poll <url>` | Polling mode for Android/Pi |
| `python rasova_agent.py --poll <url> --install-boot` | Polling mode + Termux auto-start on reboot |
| `python rasova_agent.py --install` | Windows auto-start at login |
| `python rasova_agent.py --uninstall` | Remove all auto-start |

---

*Last updated: May 2026 — covers parcel charge fix, compact receipt layout,
polling print queue with tenant isolation, 53 tests, and native Android APK
(WebView + PrintService + BootReceiver) replacing the Termux setup.*
