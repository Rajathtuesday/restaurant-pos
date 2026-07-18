# Rasova Printer Setup Guide

## What Changed (v1.2 — Printer Overhaul)

### Problem Solved
Previously, Rasova stored the printer's **IP address** in settings. IP addresses change when:
- The router restarts and DHCP assigns a new lease
- The router is replaced or factory-reset (losing DHCP reservations)
- A technician changes the network

Every time this happened, the owner had to find the new IP and update it manually — tedious and support-intensive.

### Solution
Rasova now stores the printer's **MAC address** — a permanent hardware identifier printed on a sticker on every printer. The system resolves MAC → IP automatically at print time using the OS ARP table. If that fails, it falls back through a chain until it finds the printer.

---

## Fallback Chain (automatic, no owner action needed)

```
Print job triggered
        │
        ▼
1. Try stored/last-known IP          ← instant, works 99% of the time
        │ fails
        ▼
2. ARP resolve MAC → current IP      ← finds printer even after DHCP change
        │ fails (printer just booted, ARP cache stale)
        ▼
3. Try cached last_seen_ip           ← from previous successful print
        │ fails
        ▼
4. Full subnet scan (port 9100)      ← finds any ESC/POS printer on the LAN
        │ fails
        ▼
   Error shown to cashier
```

---

## Files Changed

| File | What Changed |
|---|---|
| `tenants/models.py` | Added `printer_mac`, `agent_host`, `paper_width_mm` to `Outlet` model |
| `tenants/migrations/0019_outlet_printer_fields.py` | Migration for the three new fields |
| `rasova_agent.py` | Added `resolve_mac_to_ip()` + `_normalise_mac()`; inserted ARP step as Level 2 in `smart_network_print`; `printer_mac` now accepted in WebSocket print protocol |
| `setup/views/core_views.py` | `outlet_settings` view saves `printer_mac`, `agent_host`, `paper_width_mm` |
| `setup/templates/setup/outlet_settings.html` | New **Thermal Printer** section; moved USB toggle out of Compliance; added MAC formatter JS + agent ping test |
| `orders/templates/orders/bill.html` | `RASOVA_AGENT_URL` uses `outlet.agent_host`; `printer_mac` sent in every print job |
| `setup/templates/setup/setup_kitchen_stations.html` | `AGENT_WS` uses `outlet.agent_host`; `printer_mac` sent in test print |

---

## New Fields on Outlet Model

```python
outlet.printer_mac      # "00:1B:44:11:3A:B7"  — permanent printer identity
outlet.agent_host       # "localhost" or "192.168.1.200"
outlet.paper_width_mm   # 58 or 80
```

---

## Setup: Windows PC + USB Printer (existing setup)

**Hardware:** Windows laptop/PC → USB cable → thermal printer

### Step 1 — Find the MAC address
1. Turn the printer on
2. Flip it upside down
3. Read the sticker — look for a line like `MAC: 00:1B:44:11:3A:B7` or `MAC Address: 001B44113AB7`
4. Copy it (any format works — colons, dashes, or plain digits)

### Step 2 — Enter MAC in Rasova
1. Log in as **Owner** or **Manager**
2. Go to **Setup → Outlet Settings**
3. Scroll to **Thermal Printer** section
4. Paste the MAC address into the **Printer MAC Address** field
   - Rasova auto-formats it to `XX:XX:XX:XX:XX:XX` as you type
5. Set **Paper Width** — most printers use **80mm** (check your roll)
6. Leave **Print Agent Host** as `localhost`
7. Click **Save Outlet Details**

### Step 3 — Install and start the agent
```bash
# On the Windows billing PC — run once
python rasova_agent.py --install

# This installs auto-start at Windows login (no admin required)
# Agent runs silently in background every time Windows starts
```

### Step 4 — Enable agent printing
1. Go to **Setup → Outlet Settings → Thermal Printer**
2. Tick **Rasova Print Agent** checkbox
3. Save

### Step 5 — Test
1. Go to **Setup → Kitchen Stations**
2. Click **Select USB Printer** — your printer should appear in the list
3. Select it and click **Test Print**
4. You should see 2 partial cuts + 1 full cut on the paper
5. Green indicator = agent connected ✓

---

## Setup: Android Tablet + Network Printer (new)

**Hardware:** Android tablet → WiFi → router → network thermal printer (Ethernet/WiFi)

### Step 1 — Connect the printer to WiFi/Ethernet
1. If the printer has an **Ethernet port**: plug a cable from the printer to the router
2. If the printer has **WiFi**: follow the printer's WiFi setup (usually hold a button + print a config page)
3. Print a **network configuration page** from the printer:
   - On most printers: hold the Feed button for 3 seconds while powered on
   - This prints the current IP, MAC address, and network status

### Step 2 — Find the MAC address
From the printed network config page, copy the **MAC Address** line.
Example: `MAC Address: 00-1B-44-11-3A-B7`

> **Why MAC and not IP?** The IP on this config page will change after a router restart. The MAC never changes.

### Step 3 — Enter MAC in Rasova
1. Log in on the tablet browser → **Setup → Outlet Settings**
2. Scroll to **Thermal Printer**
3. Paste the MAC address → Rasova formats it automatically
4. Set Paper Width (80mm for most)
5. Leave **Print Agent Host** as `localhost` for now
6. Save

### Step 4A — Agent on the same tablet (Termux)

> Use this if you don't want any extra hardware.

#### Install Termux
1. Download **Termux** from F-Droid (not Play Store — the Play Store version is outdated)
   - F-Droid: `https://f-droid.org` → search Termux
2. Open Termux
3. Run:
```bash
pkg update && pkg upgrade -y
pkg install python -y
pip install websockets
```

#### Copy the agent to the tablet
```bash
# Option A: download from your Rasova server (replace with your actual URL)
curl -O https://your-rasova-server.com/static/agent/rasova_agent.py

# Option B: copy via USB from your PC
# Connect tablet via USB, copy f:\pos\rasova_agent.py to /sdcard/
# Then in Termux:
cp /sdcard/rasova_agent.py ~/rasova_agent.py
```

#### Auto-start with Termux:Boot
1. Install **Termux:Boot** from F-Droid
2. Create the boot script:
```bash
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/rasova_agent.sh << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
cd ~
python rasova_agent.py >> ~/rasova_agent.log 2>&1 &
EOF
chmod +x ~/.termux/boot/rasova_agent.sh
```
3. Open Termux:Boot once to activate it
4. Restart the tablet — agent starts automatically

#### Prevent Android from killing Termux
1. Go to Android **Settings → Battery → App Launch** (or Battery Optimization)
2. Find **Termux** → set to **Manage Manually** → enable all background options
3. Also do the same for **Termux:Boot**

> On Samsung: Settings → Device Care → Battery → Background Usage Limits → Never Sleeping Apps → Add Termux

#### Test in Termux
```bash
python ~/rasova_agent.py
# Should print:
# Rasova Print Agent v1.1.0
# Listening on ws://localhost:8765
# Platform: linux | win32print: no
```

#### Verify in Rasova
1. On the tablet browser → **Setup → Outlet Settings → Thermal Printer**
2. Agent Host should be `localhost`
3. Click **Test Agent Connection**
4. Should show: `Connected — Agent v1.1.0 on linux` ✓

---

### Step 4B — Agent on a Raspberry Pi (recommended for busy outlets)

> Use this when you want the agent always-on regardless of tablet state.
> **Hardware cost: ~₹1,500** for a Raspberry Pi Zero 2W + microSD card.

#### What you need
- Raspberry Pi Zero 2W (or any Pi)
- MicroSD card (8GB+)
- Pi powered via USB adapter (can share the printer's power strip)
- Printer connected to Pi via **USB cable**

#### Set up the Pi
1. Flash **Raspberry Pi OS Lite** to the SD card using Raspberry Pi Imager
2. Enable WiFi and SSH in the Imager settings before flashing:
   - Set WiFi SSID and password (the restaurant's WiFi)
   - Enable SSH
   - Set hostname: `rasova-agent`
3. Boot the Pi — it joins WiFi automatically

#### Find Pi's IP (first time only)
```bash
# From your laptop on the same WiFi:
ping rasova-agent.local
# Shows the IP, e.g. 192.168.1.200
```

#### Fix Pi's IP via router DHCP reservation
1. Open router admin page (usually `192.168.1.1` in browser)
2. Find **DHCP Reservation** or **Static Lease** or **IP Binding**
3. Find `rasova-agent` in the connected devices list
4. Assign it a fixed IP, e.g. `192.168.1.200`
5. Save and reboot router

> **Note:** This DHCP reservation is for the Pi, not the printer. The Pi's IP must be stable so the tablet knows where to connect. The printer's IP can change freely — the agent handles that via MAC+ARP.

#### Install agent on Pi
```bash
ssh pi@192.168.1.200

sudo apt update && sudo apt install python3-pip -y
pip3 install websockets

# Copy rasova_agent.py to the Pi
scp f:/pos/rasova_agent.py pi@192.168.1.200:~/
```

#### Auto-start on Pi using systemd
```bash
ssh pi@192.168.1.200

sudo nano /etc/systemd/system/rasova-agent.service
```

Paste this content:
```ini
[Unit]
Description=Rasova Print Agent
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/rasova_agent.py
WorkingDirectory=/home/pi
Restart=always
RestartSec=5
User=pi
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable rasova-agent
sudo systemctl start rasova-agent

# Verify it's running
sudo systemctl status rasova-agent
```

#### Update Rasova to point to the Pi
1. **Setup → Outlet Settings → Thermal Printer**
2. Change **Print Agent Host** from `localhost` to `192.168.1.200`
3. Save

#### Test
1. **Setup → Outlet Settings → Thermal Printer**
2. Click **Test Agent Connection**
3. Should show: `Connected — Agent v1.1.0 on linux` ✓
4. Go to **Kitchen Stations → Test Print** to verify paper prints correctly

---

## Day-to-Day: What Happens When the Router Restarts

| Situation | What Rasova Does | Owner Action |
|---|---|---|
| Router restarts, printer gets new DHCP IP | Agent ARP-resolves MAC → finds new IP automatically | Nothing |
| Router replaced, DHCP reservations lost | Same — MAC is permanent, ARP still works | Nothing |
| Printer powered off, ARP cache stale | Falls back to subnet scan, finds printer | Nothing |
| Printer replaced with new hardware | New printer has new MAC | Update MAC in Outlet Settings once |
| Pi/agent device changes IP | Pi IP must be stable (DHCP reservation at router) | Update agent_host in Outlet Settings once |

---

## Troubleshooting

### "Test Agent Connection" says "Cannot reach agent"

**Windows:**
- Open Task Manager → check if `pythonw.exe` is running
- If not: open Command Prompt → `python rasova_agent.py`
- Re-run `python rasova_agent.py --install` to fix auto-start

**Android Termux:**
- Open Termux → run `python ~/rasova_agent.py`
- Check battery optimization is disabled for Termux
- Check Termux:Boot is installed and opened at least once

**Raspberry Pi:**
```bash
ssh pi@192.168.1.200
sudo systemctl status rasova-agent
# If stopped:
sudo systemctl restart rasova-agent
```

---

### Print job fails with "Network print failed"

1. Print a network config page from the printer (hold Feed 3 seconds)
2. Confirm the printer is on the same WiFi/LAN as the device
3. Go to **Kitchen Stations → Discover Network Printers** — does the printer appear?
4. If not, check the printer's Ethernet cable or reconnect it to WiFi

---

### MAC address not on the printer

Some printers only show the MAC in their **network config printout**, not on the label.
1. Hold the **Feed** button for 3–5 seconds while the printer is on
2. It prints a status page — the MAC is on that page
3. Alternatively: connect the printer to the network, open router admin → connected devices → find the printer → copy its MAC

---

## WebSocket Protocol Reference (updated)

The `print` message now accepts `printer_mac`:

```json
{
  "type": "print",
  "printer": "BillTouch ZY306",
  "network_host": "192.168.1.100",
  "network_port": 9100,
  "lines": ["...ESC/POS bytes..."],
  "job_id": "bill-123",
  "encoding": "cp437",
  "outlet_id": "my-outlet",
  "printer_mac": "00:1B:44:11:3A:B7"
}
```

If `network_host` is wrong or empty but `printer_mac` is set, the agent ARP-resolves the MAC to find the current IP before falling back to subnet scan.
