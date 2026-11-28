# Setting up a new restaurant on Rasova

From "a restaurant said yes" to "its first bill printed", in three parts:

| Part | Who | Time |
|---|---|---|
| [A. Rasova creates the restaurant](#part-a-rasova-creates-the-restaurant) | Rasova (a superuser) | 5 minutes |
| [B. The owner sets it up](#part-b-the-owner-sets-it-up) | The restaurant's owner, with Rasova on the phone if they like | 20 to 30 minutes |
| [C. The go-live check](#part-c-the-go-live-check) | Both, at the restaurant | 10 minutes |

Written 28 November 2026 against the screens as they are on rasova.net. A Rasova box (no internet)
is set up differently: see [`BOX_GUIDE.md`](BOX_GUIDE.md).

---

## Before you start: what to ask the restaurant

- **Name**, exactly as it should print on bills.
- **Type**: Pub / Bar, Fine Dining, QSR / Fast Food, or Cafe. The type switches on the tools that kind of place
  needs (a pub gets liquor VAT, happy hours, licensed hours, kegs, covers).
- **Address, phone, GSTIN** (if registered) and **FSSAI** number: they print on every bill.
- **GST pricing**: are menu prices *with* GST (usual for QSR and cafes) or is GST *added on top* (usual for
  fine dining and pubs)?
- **The menu**, any one of:
  - a clear photo or PDF of the paper menu (Rasova's AI reads it),
  - a **Rasova menu file** (`.json`), saved from another outlet with Menu, Menu file, Save,
  - or nothing: they type their dishes in.
- **Staff**: names and roles (owner, manager, cashier, captain or waiter, chef).
- **Tables**: how many, and how they are named (T1, T2... or by area).
- **Payments**: cash, UPI (their UPI ID prints on bills), card.
- **Printers**: how many thermal printers (bill counter, kitchen, bar) and each one's network address (IP), or a
  PC at the counter. No printer yet? The kitchen screen works without one.

---

## Part A: Rasova creates the restaurant

1. Sign in at **rasova.net/login** with your superuser account, then open **rasova.net/superuser/**.
2. Fill in **Add a Restaurant**:

   | Field | Notes |
   |---|---|
   | Restaurant name | Also makes the web address: "Copper Tap" becomes **copper-tap.rasova.net** (`-2` is added if taken). |
   | Type | Pick the right one: it switches that type's features on. |
   | Outlet name | Optional ("Main Branch" if empty). |
   | GSTIN, phone | Optional; a GSTIN is checked. |
   | Owner username and password | The owner's login. Lowercase username, at least 8 characters of password. |

3. **Create Restaurant** makes, in one go: the restaurant, its first outlet, the owner's login, a "Counter"
   kitchen station and the payment setup.
4. Click the restaurant in the list to open its settings page:
   - **Presets**: apply the one for its type (for a pub: **Pub / Bar**, which also caps a cashier's or captain's
     own discount at 10%; more needs a manager).
   - **Feature Flags**: switch single features on or off.
   - **Outlet Info**: GSTIN, FSSAI, the address printed on bills, SAC code, GST pricing.
   - **Rasova Subscription**: the monthly fee (Rs 999) and status.
   - **Printers / Kitchen Stations** and **Payment Methods** can be set here too, or left for the owner.
5. Send the owner three things: their address (`https://copper-tap.rasova.net/login/`), the username, and the
   password, which they should change.

**One browser holds one login for every rasova.net address.** Signing in as the owner in the browser you use as
superuser signs your superuser out there. To be both at once, use a second browser or an incognito window.

---

## Part B: The owner sets it up

The owner signs in at their address. Rasova opens a five-step setup; each step can be redone later from **Setup**,
and the **"Setup 3/5 complete"** badge in the corner shows what is left.

1. **Restaurant info**: name, type, web address, address, phone, FSSAI, GST pricing, GSTIN.
   Changing the **web address** here changes the address everyone uses: only change it on purpose, and tell the staff.
2. **First menu items**, any one of:
   - **AI Import, a photo**: tap the card, choose a photo or PDF of the menu. Rasova reads it (about half a
     minute) and says how many dishes it added. iPhone HEIC photos are not read: take a screenshot instead.
   - **AI Import, a Rasova menu file**: choose the `.json` on the same card. Rasova opens Menu file's check
     screen ("Ready to load: 33 dishes in 7 sections") and **Load into ...** adds them. Nothing is removed, and a
     dish with the same name in the same section is updated.
   - **Load Sample Menu**: 8 dishes in 3 sections, to try Rasova before the real menu.
   - **By hand**: a section and its first dishes, here; the rest on the Menu page.
3. **Staff**: one login now (lowercase username, role, password). More later in **Setup, Staff**.
4. **Tables**: a number and a prefix (8 and "T" make T1 to T8), or one table by name.
5. **Payment methods**: Cash, UPI (with the UPI ID), Card. **Finish** opens the dashboard.

### After the five steps

- **Menu** (the Menu page): check prices and tax, set each dish's station (Kitchen or Bar: it decides which
  printer gets the ticket), add modifiers (sauces, mixers). The same AI import and the **Menu File** page are in the
  page's **More options** (on a phone: the header's **More**, then **More options**).
- **Printers** (**Setup, Printers**): for each printer, type its address and paper width, **Save**, then **Test
  print**: the row says "Printed. The printer took the test page." To find a printer's address, hold its Feed button
  while switching it on: it prints a page with its IP.
- **Printing needs one device in the restaurant**, on the same Wi-Fi as the printers, to collect Rasova's print queue:
  - **A PC at the counter running the Rasova print agent.** Download it on **Setup, Printers, The counter PC**, install
    Python, then:
    - bills and KOTs made in **that PC's browser**: run `python rasova_agent.py --install` once (it starts with
      Windows from then on);
    - orders taken **on phones** and printed by that PC: run
      `python rasova_agent.py --server https://copper-tap.rasova.net --key <the outlet's print key>`.
      The print key is not on any screen yet: ask Rasova for it.
    - More detail: [`PRINTER_SETUP.md`](PRINTER_SETUP.md).
  - **Or a Rasova box**, which prints by itself and works without internet: [`BOX_GUIDE.md`](BOX_GUIDE.md).
  - The Android print app is paused and no longer offered on rasova.net (27 November 2026).
- **Staff** (**Setup, Staff**): logins and roles. Each manager sets an approval PIN (**Setup, Approval PIN**): a
  discount or a void on a printed bill asks for it.
- **Outlet Settings** (**Setup, Outlet Settings**): the **staff discount limit** (off by default, so otherwise a
  cashier or captain can give any discount). For a pub, also the **licensed hours**: liquor can't be ordered outside them.
- **Pub tools**, if it is a pub: happy hours and other offers (**Setup, Offers**), kegs (**Inventory, Kegs**), the
  nightly bar count (**Inventory, Bar Count**), cover charges at the door (**Covers**). How each works:
  [`USER_MANUAL.md`](USER_MANUAL.md).

---

## Part C: The go-live check

Do this before the first guest, with the printers switched on:

- [ ] On a phone, open a table, add one dish and one drink, **send**: the kitchen ticket and the bar ticket print
      (or appear on the kitchen screen).
- [ ] Ask for the bill: it prints at the counter with the right name, address, GSTIN and taxes.
- [ ] Take a cash payment: the table is free again.
- [ ] **Reports** show the test sale (it stays in today's numbers).
- [ ] Each staff member signs in once on their own phone or the counter PC.
- [ ] A manager gives a small discount on the printed bill: Rasova asks for the PIN.
- [ ] For a pub: try a spirit after the licensed hours (Rasova refuses it), and check a happy-hour price.

---

## When something goes wrong during setup

| What you see | What it means | What to do |
|---|---|---|
| "AI import failed: Couldn't read that file..." | The photo or file can't be read | A clear JPG or PNG, a PDF, or a screenshot; not an iPhone HEIC photo. |
| "That's a Rasova menu file, not a menu to read..." | An old copy of the page | Reload the page; the menu file then goes straight to its check. |
| "Invalid username or password." | A typo, or capitals | Usernames are lowercase, exactly as given. |
| "Too many wrong passwords for this name..." | Locked for an hour | The owner or a manager: **Setup, Staff**, **Unlock**. |
| "That address belongs to another restaurant. You're signed in to ..." | This browser is signed in to a different restaurant | One login per browser: sign out, or use another browser. |
| A test print stays "Sending a test page..." | Nothing is collecting the queue | Is the print agent running on the counter PC, on the printers' Wi-Fi? |
| A test print fails | Wrong address, printer off, or another Wi-Fi | Check the printer's IP page (hold Feed while switching on). |
| Setup step 2 says "Import failed. Try again." at once | A page from before 28 November | Reload the page. |

Still stuck: Rasova reads the server's logs. [`RUNBOOK.md`](RUNBOOK.md) has the commands.
