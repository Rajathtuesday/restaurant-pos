# The Rasova Box: How to Use It

### Building, installing, licensing, updating and looking after a Rasova box, step by step

The Rasova box is the whole of Rasova running on one small computer inside a restaurant, reached over the restaurant's own Wi-Fi at `http://rasova.local`, with no internet needed. It is the same app as the cloud, compiled to machine code so no source code is on the box.

This guide is for whoever at Rasova builds releases, sets up boxes and supports them. Section 7 is what the restaurant's owner sees, so it can be read out to them.

---

## Table of Contents

1. [What's in it](#1-whats-in-it)
2. [Your two signing keys (do this first)](#2-your-two-signing-keys-do-this-first)
3. [Build a release on your PC](#3-build-a-release-on-your-pc)
4. [Set up a new box at your desk](#4-set-up-a-new-box-at-your-desk)
5. [At the restaurant: first switch-on](#5-at-the-restaurant-first-switch-on)
6. [Licences](#6-licences)
7. [Every day: what the owner sees](#7-every-day-what-the-owner-sees)
8. [Updates](#8-updates)
9. [Backups and restore](#9-backups-and-restore)
10. [When a box dies](#10-when-a-box-dies)
11. [Printing on a box](#11-printing-on-a-box)
12. [Command reference](#12-command-reference)
13. [Troubleshooting](#13-troubleshooting)
14. [What is off on a box](#14-what-is-off-on-a-box)
15. [Trying a box on your own PC](#15-trying-a-box-on-your-own-pc)
16. [For developers](#16-for-developers)
17. [Not done yet](#17-not-done-yet)

---

## 1. What's in it

On the box, five parts run in Docker, all from one signed bundle:

| Part | What it does |
|---|---|
| `web` | Rasova itself (compiled), answering every tablet, phone and laptop |
| `worker` | Prints KOTs, bills and queued jobs straight to the printers' IPs; makes the 4:30 am backup |
| `db` | Postgres 15: every bill is on disk before it says saved |
| `redis` | Holds print jobs waiting for a printer, kept on disk too |
| `rasova-box` | The script on the box that installs, updates, backs up, restores and rolls back |

In the repository:

| Where | What |
|---|---|
| `box/` | The box app: edition switch, first-run setup, licence, clock guard, backups, restore, update checks, Box health page, the box's own print queue |
| `box/docker/compile.py` | Turns every module into machine code (Cython) and fails if any source is left |
| `box/docker/Dockerfile` | Builds the image (`--target box`) and the compiled test image (`--target test`) |
| `box/docker/Dockerfile.dockerignore` | The allowlist of what goes into the build (never `.env`, `.git`, docs) |
| `box/docker/compose.yml` | How the five parts run on a box |
| `box/docker/entrypoint.sh` | What each container runs (web: migrate then serve; worker: both Celery queues) |
| `box/host/install.sh` | Turns a fresh Ubuntu 24.04 mini PC into a box |
| `box/host/rasova-box` | The box's own command (copied to `/usr/local/bin/rasova-box`) |
| `devtools/management/commands/sign_box_license.py` | Makes a licence file (your PC only) |
| `devtools/management/commands/make_box_bundle.py` | Makes a signed update bundle (your PC only) |

On a box, everything lives in `/srv/rasova`:

```
/srv/rasova/
├── compose.yml       how the five parts run (from the bundle)
├── .env              which version runs (RASOVA_VERSION=...)
├── data/
│   ├── box.env       this box's secrets (made at install, never in an image)
│   ├── license.json  the licence
│   └── logs/         the app's logs (survive updates)
├── backups/          nightly backups (the backup drive is mounted here)
└── versions/         the previous version's compose.yml and the last update's record
```

---

## 2. Your two signing keys (do this first)

Two private keys live **only** on your PC, in `C:\Users\Rajat\.rasova_box\`:

| File | Signs | The box trusts it through |
|---|---|---|
| `license_signing_key.pem` | Licence files | `LICENSE_PUBLIC_KEY_B64` in `box/license.py` |
| `update_signing_key.pem` | Update bundles | `UPDATE_PUBLIC_KEY_B64` in `box/updates.py`, and `UPDATE_KEY_PEM` in `box/host/rasova-box` |

Rules:

- **Back them both up today**: an offline USB stick kept in a drawer, plus your password manager.
- Never commit them, never email them, never paste them anywhere, never copy them onto a box.
- **If you lose them**, you can't make licences or updates for any box that exists.
- **If they leak**, anyone can make licences (licence key) or updates that boxes accept (update key).

Changing a key (only if one leaks):

1. Make a new pair on your PC:
   ```bash
   python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K; from cryptography.hazmat.primitives import serialization as S; import base64; k=K.generate(); open('new_key.pem','wb').write(k.private_bytes(S.Encoding.PEM,S.PrivateFormat.PKCS8,S.NoEncryption())); print(base64.b64encode(k.public_key().public_bytes(S.Encoding.Raw,S.PublicFormat.Raw)).decode())"
   ```
2. Put the printed public half into the code: `box/license.py` for the licence key, or `box/updates.py` plus `box/host/rasova-box` for the update key (the script's copy is PEM).
3. For the **licence key**: every licence signed with the old key stops working the moment a box runs the update with the new public key. So sign each box's new licence with the new key first, and add it on Box health right after that box's update, in the same visit.
4. For the **update key**: sign the next bundle with the **old** key (it's the only one the boxes trust). That bundle carries the new key, and from then on only the new key works. The `rasova-box` script on each box is not replaced by bundles, so copy the new script onto each box by hand (section 8).

---

## 3. Build a release on your PC

You need Docker Desktop running, the repository's virtual environment, and both keys.

**1. Pick the version.** Use `1.0.0` style numbers. `0.9.x` were test builds.

**2. Build the image** (about 8 minutes; it compiles every module):
```bash
docker build -f box/docker/Dockerfile --target box -t rasova-box:1.0.0 --build-arg RASOVA_VERSION=1.0.0 .
```
The build stops by itself if any source file would be left in the image.

**3. Check no source is inside** (prints nothing when all is well):
```bash
docker run --rm --entrypoint sh rasova-box:1.0.0 -c 'find /app -name "*.py" -not -path "*/migrations/*" -not -name "__init__.py" -not -name manage.py'
```

**4. Run the whole test suite on the compiled code** (about 25 minutes; reuses the compile from step 2):
```bash
docker build -f box/docker/Dockerfile --target test -t rasova-box-test:1.0.0 .
docker network create rasova-testnet
docker run -d --name rasova-testdb --network rasova-testnet -e POSTGRES_DB=rasova_test -e POSTGRES_USER=rasova -e POSTGRES_PASSWORD=testpass --shm-size 512m postgres:15-bookworm -c max_connections=200
docker run -d --name rasova-testredis --network rasova-testnet redis:7-alpine
docker run --rm --network rasova-testnet -e SECRET_KEY=test-only -e FIELD_ENCRYPTION_KEY=K_XCqmy_iTNLU_imyLyNX0_CVDTlGSQR-kgTQK1ZOno= -e DEBUG=True -e ALLOWED_HOSTS=localhost,127.0.0.1 -e DB_ENGINE=django.db.backends.postgresql -e DB_NAME=rasova_test -e DB_USER=rasova -e DB_PASSWORD=testpass -e DB_HOST=rasova-testdb -e DB_PORT=5432 -e REDIS_URL=redis://rasova-testredis:6379/0 rasova-box-test:1.0.0 python manage.py test --noinput --parallel 8 --keepdb
```
(The network and the two containers only need making once; after that, `docker start rasova-testdb rasova-testredis`.) Expect everything to pass. Some tests skip there on purpose: the browser and JavaScript tests (no Chrome or Node in the image), and the ones that read files a box doesn't carry (the marketing site, the print agent installer, the source itself).

**5. Make the signed bundle.** The first time only, fetch the database and Redis images: `docker pull postgres:15-bookworm` and `docker pull redis:7-alpine`. Then:
```bash
python manage.py make_box_bundle --release 1.0.0 --out F:\bundles\rasova-box-1.0.0
```
This makes a folder of about 315 MB: `images.tar.gz` (Rasova, Postgres and Redis: a box never downloads anything), `compose.yml`, `manifest.json` (every file's fingerprint) and `manifest.sig` (your signature). Use `--release`, not `--version` (that one is Django's own).

**6. Fill a pen drive** with:
- the bundle folder (`rasova-box-1.0.0`)
- `box/host/install.sh`
- `box/host/rasova-box`

---

## 4. Set up a new box at your desk

**What to buy:** a mini PC (Intel N100 class, 8 GB RAM, 256 GB SSD is plenty), a USB stick or second SSD of 32 GB or more for backups, and a small UPS for the box **and** the restaurant's router.

**1. BIOS.** Set "Restore on AC power loss" to **Power On** (the box comes back by itself after a power cut) and set a BIOS password.

**2. Ubuntu.** Install **Ubuntu Server 24.04 LTS**. Choose **Encrypt the disk** (LUKS; with TPM unlock if offered, otherwise a passphrase typed at each boot). A stolen box then gives away nothing.

**3. The backup drive.** Find it with `lsblk`, then format it (this erases it):
```bash
sudo mkfs.ext4 -L rasova-backups /dev/sdX1
```

**4. Mount the pen drive:**
```bash
sudo mkdir -p /mnt/usb && sudo mount /dev/sdY1 /mnt/usb
```

**5. Run the installer.** It needs the internet this one time, to install Docker:
```bash
sudo bash /mnt/usb/install.sh /mnt/usb/rasova-box-1.0.0 --backup-disk /dev/sdX1
```
In eight steps it:
1. installs Docker;
2. names the box `rasova` (so it answers as `rasova.local`);
3. turns the firewall on with only the app's port open, and remote login (SSH) off;
4. stops the box from sleeping, caps the system logs and sets Indian time;
5. turns off automatic system updates;
6. mounts the backup drive at `/srv/rasova/backups` (the box still starts without it);
7. installs `rasova-box`;
8. runs `rasova-box install`.

Add `--keep-ssh` only if you want to log in over the network.

`rasova-box install` checks the bundle's signature and fingerprints, loads the images, makes this box's secrets (`data/box.env`), and starts everything. The first start builds the database, which takes a few minutes. It ends by printing the address.

**6. Note the box id:**
```bash
sudo rasova-box status
```
Look for `"box_id"` (16 characters: digits and the letters a to f). You need it for the licence. The 14-day trial starts when the restaurant is set up, so you can also sign the licence later.

---

## 5. At the restaurant: first switch-on

**1. Power and cable.** Plug the box and the router into the UPS. Prefer an Ethernet cable from the box to the router over Wi-Fi.

**2. Fixed address.** On the router, give the box a fixed IP (a DHCP reservation), for example `192.168.1.50`. Do the same for every printer. Write `http://192.168.1.50` on a sticker on the box and at the counter. Laptops and iPhones open `http://rasova.local`, but **many Android phones can't**, and they need the IP.

**3. Setup.** Open the address on the owner's phone or laptop: the setup page appears. Fill in the restaurant's name, its type (Pub is first in the list), the outlet's name and GSTIN, and the owner's username and password (8 characters or more). The owner is signed in and taken to the onboarding wizard (`/setup/onboard/`). Setup can only run once.

**4. Menu, tables and stations,** through the wizard.

**5. Printers.** In **Setup, Kitchen Stations**, give each station its printer's IP. Then **Setup, Printer, Test print**: the box prints the test page itself (it says "Test sent: the box prints it in a few seconds").

**6. Approval PINs.** Each manager sets their own in **Setup, Approval PIN**.

**7. Devices.** Staff use **Chrome** on their tablets and phones, at the box's address. The Rasova Android app is for the cloud: it only talks to rasova.net over https, and a box is plain http on the local network.

**8. Licence.** Add it on **Box health** (section 6).

---

## 6. Licences

A licence is a small file signed with your licence key. The box checks the signature itself, with no internet. It is tied to that box's id, so it doesn't work on another box.

**Make one** (on your PC, in the repository):
```bash
python manage.py sign_box_license --box-id 3f2a9c0b7d1e4a56 --customer "Copper Tap" --expires 2027-10-31 --out copper_tap_2027.json
```
The command refuses a box id that isn't 16 characters of digits and the letters a to f. A box with no machine id calls itself `unknown`, and a licence for that would fit every such box.

**Get it onto the box.** Send the file to the owner (WhatsApp or email). The owner opens **Box health**, presses **Add licence file** and picks it. Only the owner can do this. A file that isn't a real, unedited licence for this box is refused, and nothing changes.

**Renew** the same way, with a file that has a later date.

| State | When | What works |
|---|---|---|
| Trial | No licence yet, 14 days from setup | Everything; a reminder in the last 5 days |
| Valid | Before the end date | Everything; a reminder in the last 15 days |
| Grace | Up to 14 days after the end date | Everything, with a warning on every page |
| Ended (also: trial over) | After that | Open bills can still be billed and paid, reports still open, **no new order can start** |
| Moved | A real licence made for another box (a replacement box restored from backups) | Everything, for 14 days from when this box first saw it |
| Wrong box | A licence for another box, after those 14 days | As Ended |
| Invalid | Edited or damaged file | As Ended |

A licence never cuts off a bill in the middle of service. It only stops new orders from starting.

---

## 7. Every day: what the owner sees

**Box health** (the **Box Health** card on the Setup page, or `/box/health/`; owner and managers can look, only the owner can change things):

- **Licence**: its state, the end date, this box's id, and the Add licence file button.
- **Clock**: the time the box thinks it is. Bills, the business day and liquor hours all follow it.
- **Backups**: the last backup and whether it worked, the **Back up now** button, and the list of backups on the drive.
- **Disk**: free space.
- **Printers**: each station's printer IP.

A coloured **banner on every page** says when something needs attention:

| Banner | What to do |
|---|---|
| Trial or licence ending soon / ended | Send Rasova the box id; add the new licence file |
| This licence was made for another Rasova box | Send Rasova this box's id within the days shown |
| The clock went back, or jumped | Set the right time (section 13), then press **I've set the right time** on Box health |
| Last night's backup failed | Check the backup drive is plugged in; press **Back up now** |
| No backup in the last day and a half | Same |
| The box's disk is nearly full | Call Rasova |

Backups happen by themselves every night at **4:30 am**, after a pub's 1 am close. Box health also says if the backups are on the same drive as the bills (the backup drive isn't plugged in): one dead drive would then take both.

---

## 8. Updates

**1. Make the bundle** on your PC (section 3, steps 2 to 6).

**2. At the box, after closing time,** plug in the pen drive and mount it (section 4, step 4).

**3. Run:**
```bash
sudo rasova-box update /mnt/usb/rasova-box-1.0.1
```

What happens:
1. The bundle is **copied to the box's disk** first (pulling the pen drive out halfway can't hurt).
2. The signature and every file's fingerprint are checked. The running version checks them a second time.
3. It **refuses if bills are still open**, unless you add `--now`.
4. It makes a **backup**.
5. It loads the new version and starts it (the database changes run then).
6. It waits until the new version is healthy and its pages load.
7. If it isn't (it crashes, or doesn't come up), it **goes back by itself** to the old version and the backup from step 4, so even a half-done database change disappears. The crash is noticed within a few restarts; it doesn't wait the full 15 minutes.

The old version stays on the box after a good update, so `rollback` can use it. A test update took about a minute; the self-undo of a broken one about two.

| Message | Meaning |
|---|---|
| `the update is not signed by Rasova; nothing was changed` | Edited, or not from you. Remake the bundle |
| `... is damaged or changed (its fingerprint doesn't match)` | A file broke on the way. Copy it to the pen drive again |
| `the update is missing its manifest or signature` | Incomplete folder |
| `1 bill is still open. Update after closing time...` | Close the bills, or add `--now` |
| `not enough free disk for this update` | Free space first (call Rasova) |
| `this box already runs 1.0.1` | Nothing to do |
| `the update failed and was undone: 1.0.0 is running with everything as it was` | The new version didn't start; look at the logs with `sudo rasova-box logs web` |

**Undo an update by hand** (a power cut in the middle of it, or a problem found the next morning):
```bash
sudo rasova-box rollback
```
It goes back to the previous version and the backup taken just before the update, after you type `ROLLBACK`. Bills made since the update come out of the app, but a backup of them is made first, so Rasova can still get them back. It works once per update.

**When `rasova-box` itself changes:** bundles replace the app, Postgres, Redis and `compose.yml`, but not the `rasova-box` script on the box. Copy a new one over by hand:
```bash
sudo install -m 755 /mnt/usb/rasova-box /usr/local/bin/rasova-box
```

---

## 9. Backups and restore

Backups are in `/srv/rasova/backups` (the backup drive), named `rasova-YYYYMMDD-HHMMSS.dump`. The newest 14 are kept. A copy of the licence (`license.json`) sits next to them.

Each one is written to a temporary name, read back to check it's complete, and only then given its real name, so a power cut or a full drive never leaves something that looks like a backup but isn't. Only one runs at a time.

**A backup now:**
```bash
sudo rasova-box backup
```
(or **Back up now** on Box health)

**Restore:**
```bash
sudo rasova-box restore                                  # lists the backups, newest first
sudo rasova-box restore rasova-20261020-221441.dump      # asks you to type RESTORE
```
The app stops for about a minute while it runs. The backup is loaded into a **new** database, and only when that has fully worked is it swapped in. So a damaged backup changes nothing, and nothing made after the backup is left behind.

**Undo a restore** (the wrong file was picked). The database that was replaced is kept once, as `rasova_before_restore`:
```bash
cd /srv/rasova
sudo docker compose stop web worker
sudo docker compose exec db psql -U rasova -d postgres -c 'ALTER DATABASE rasova RENAME TO rasova_wrong_restore' -c 'ALTER DATABASE rasova_before_restore RENAME TO rasova'
sudo docker compose up -d
```
The next restore replaces `rasova_before_restore`, so there is one level of undo.

**A copy off the box** (for a second place): plug in another USB stick, mount it, and run:
```bash
sudo cp /srv/rasova/backups/rasova-*.dump /srv/rasova/backups/license.json /mnt/other/
```

---

## 10. When a box dies

1. Set up a new box at your desk (section 4) with **the same backup drive** (`--backup-disk`), so the old backups are there.
2. Take it to the restaurant, then restore the newest backup:
   ```bash
   sudo rasova-box restore
   sudo rasova-box restore rasova-YYYYMMDD-HHMMSS.dump
   ```
   It says `licence taken from the backup drive`: the licence travels with the backups.
3. Staff sign in with their usual usernames and passwords. Box health shows the licence as **Moved**, and everything works for 14 days.
4. Sign a licence for the new box's id (`sudo rasova-box status`) and add it on Box health.

If the backup drive died with the box, the bills since the last copy you made off the box (section 9) are gone. That's why the copy-off step matters.

---

## 11. Printing on a box

A box is on the restaurant's network, so it prints straight to each printer's IP on port 9100. There's no agent, no phone app and no QZ Tray needed.

| What | How it prints on a box |
|---|---|
| KOTs (send to kitchen) | The box's worker sends them to each station's printer |
| The bill, paid in Chrome | The box sends it to the default station's printer |
| Setup's Test print, and any job in the print queue | The box's worker prints the queue itself (`box/print_queue.py`) |
| Browser print (the print dialog) | Still there as the fallback, as in the cloud |

The queue is claimed the way the agents claim it, so a job prints once even if someone also points an agent at the box. A job whose printer doesn't answer is marked failed, with the reason. Give every printer a fixed IP on the router (section 5).

---

## 12. Command reference

**On the box** (`sudo` for anything that changes something):

| Command | What it does |
|---|---|
| `rasova-box install <bundle>` | First time: check, load, make the secrets, start |
| `rasova-box status` | The parts' state, version, box id, licence, backups, open bills, disk |
| `rasova-box backup` | A backup now |
| `rasova-box restore [<file>]` | List backups, or put one back (asks first; `--yes` skips the question) |
| `rasova-box update <bundle> [--now]` | Check, back up, install, health-check, undo itself on failure |
| `rasova-box rollback` | Undo the last update (asks first; `--yes` skips the question) |
| `rasova-box logs [web\|worker\|db\|redis]` | The last 200 lines of a part's log (web if none given) |
| `rasova-box start` / `stop` / `restart` | All five parts |

**Inside the app on the box** (`cd /srv/rasova` first):

```bash
sudo docker compose exec web python manage.py box_status                   # the status as JSON
sudo docker compose exec web python manage.py box_backup                   # what rasova-box backup runs
sudo docker compose exec web python manage.py changepassword <username>    # a forgotten password
```
`box_restore` and `box_verify_update` exist too, but run them through `rasova-box` (it stops the app first, and checks bundles twice).

**On your PC:**

| Command | What it does |
|---|---|
| `python manage.py sign_box_license --box-id ... --customer ... --expires YYYY-MM-DD --out file.json` | A licence |
| `python manage.py make_box_bundle --release X --out <folder>` | A signed bundle of the image `rasova-box:X` |

---

## 13. Troubleshooting

| Problem | Check | Fix |
|---|---|---|
| A phone can't open `rasova.local` | Is it Android? | Use the IP on the sticker (section 5) |
| Nothing opens at all | Is the box on? Is the router on? | `sudo rasova-box status`; `sudo rasova-box restart` |
| A page says "not found" | Is it a QR menu, WhatsApp, Zomato, Razorpay or demo page? | Those are off on a box (section 14) |
| KOTs don't print | The station's printer IP, the printer on and on the same router | **Setup, Printer, Test print**; `sudo rasova-box logs worker` |
| The clock warning shows | The box's time | `sudo timedatectl set-time "2026-10-21 19:30:00"`, then **I've set the right time** on Box health |
| Backup failed | Is the backup drive plugged in? `df -h /srv/rasova/backups` | Plug it in, then **Back up now** |
| "No new order can start" mid-service | The licence state on Box health | Open bills still finish. Sign a licence for this box's id and add it on Box health |
| The owner forgot their password | | `changepassword` (section 12) |
| An update was undone | `sudo rasova-box logs web` | Fix the release, make a new bundle |
| The web part keeps restarting | `sudo rasova-box logs web` | `sudo rasova-box rollback` if it followed an update |

---

## 14. What is off on a box

These need the internet or a guest's phone. On a box they are off **whatever the feature switches say**, and their pages answer "not found", so an old printed QR code gets a plain 404:

- **QR ordering**, waiter call, guest feedback (guests' phones aren't on the staff Wi-Fi)
- **Zomato / Swiggy** order sync
- **Razorpay** online payments (UPI on the card machine still works, recorded as UPI)
- **WhatsApp** receipts and public bill links, and **email** reports
- The **menu and recipe importers**
- The marketing site, demo, live demo, the superuser panel (there is one restaurant) and the print agent download

Everything else works: tables, KOTs, bills, discounts and approval PINs, liquor hours, bar count, kegs, covers, the liquor register, offers and promos, inventory, shifts, reports and exports.

---

## 15. Trying a box on your own PC

`rasova-box` has a test mode for running a box under Docker Desktop on Windows (Git Bash):

```bash
export RASOVA_DEV=1 MSYS_NO_PATHCONV=1           # no root, no chown; no Git Bash path rewriting
export RASOVA_HOME=C:/rasova-test/home            # instead of /srv/rasova
mkdir -p $RASOVA_HOME
echo "4f1c9e0b7d2a4e6f8a1b3c5d7e9f0a12" > C:/rasova-test/machine-id
printf 'HTTP_PORT=8089\nMACHINE_ID_FILE=C:/rasova-test/machine-id\n' > $RASOVA_HOME/.env
bash box/host/rasova-box install F:/bundles/rasova-box-1.0.0
```
Then open `http://localhost:8089`. All the other commands work the same way.

- **A second box** on the same PC: another `RASOVA_HOME`, and in its `.env` a different `HTTP_PORT` and `MACHINE_ID_FILE`, plus `COMPOSE_PROJECT_NAME=rasova2`.
- **A pretend printer:** a small Python script listening on port 9100 that saves what it receives, in a container on the box's network (`docker run -d --network rasova_default ...`). Give its container IP to a station.

---

## 16. For developers

- **The switch.** `RASOVA_EDITION=box` sets `settings.BOX`; code asks `box.edition.is_box()`. Box tests switch it on with `override_settings(BOX=True)`.
- **A new feature that needs the internet** or a guest's phone: add its flag to `BOX_OFF_FEATURES` and its URLs to `OFF_PATHS` in `box/edition.py`.
- **A new Django app:** add `!appname` to `box/docker/Dockerfile.dockerignore`, or it won't be in the box image. `BoxBuildListTest` fails until you do.
- **Celery.** The box's worker must listen to both queues (`--queues default,printing`, in `entrypoint.sh`). Its beat schedule is only the nightly backup (`core/settings.py`).
- **Addresses.** A box has no fixed address and no subdomains: login stays on the address typed, and links are built from the request (`BASE_URL` is empty on a box).
- **Compiling.** Migrations stay as source (their names start with a digit, which a compiled module can't have); so do `manage.py` and `__init__.py`. Templates, CSS and JavaScript are served as they are.
- **Tests.** `box/tests/test_box.py`; CI runs the `box` app. In the compiled test image, `RASOVA_COMPILED_TESTS=1` makes tests that read files a box doesn't carry skip there, and only there (`core.testing.not_in_compiled_build`). A file missing from the repository still fails CI.
- **Line endings.** `box/host/rasova-box` and every `.sh` are kept LF by `.gitattributes`. A CRLF script fails inside Linux.

---

## 17. Not done yet

- `install.sh` hasn't yet run on a real mini PC: everything so far ran under Docker Desktop.
- No pull-the-plug test during service yet, and no test with real thermal printers on a box.
- Bundles don't update the `rasova-box` script itself (section 8).
- A box doesn't sync with the cloud: the owner sees reports at the restaurant, not from home.
