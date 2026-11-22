"""
End to end against a real Rasova box, over the network, the way a phone sees it.

    python scripts/box_e2e.py http://192.168.1.50 <a printer's IP>

Run it on a FRESH box (it does the first-run setup itself, as "Edge Pub",
owner edge_owner) from a laptop on the box's network, with one printer
switched on at <printer IP>. It checks: first run; the Android app served
by the box as an Android app; http and no print agent key in the pages;
Connect a device and its shortcut; Setup > Printers (a zero-padded
address, a typo, a test page the box itself prints, a printer that never
answers named on Printers, Setup and Box health); the old printer page's
redirect; a menu file loaded through the page; the Updates panel. It needs nothing but `requests`. Run on the box itself (or a PC
running it under Docker), BOX_E2E_ON_THE_BOX=1 adds the localhost check.

First run 22 Nov 2026 against the box image under compose on a PC, with a
pretend printer (a Python listener on port 9100): 28 of 28. The real run
belongs to the first mini PC session (docs/BOX_GUIDE.md, section 18).
"""
import json
import os
import re
import sys
import time

import requests

BASE, PRINTER = sys.argv[1].rstrip("/"), sys.argv[2]
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def csrf(s, url):
    page = s.get(url)
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text)
    return page, (m.group(1) if m else s.cookies.get("csrftoken"))


s = requests.Session()

# 1. Before setup every page goes to the first-run page.
r = s.get(BASE + "/dashboard/", allow_redirects=False)
check("before setup, a page goes to /box/setup/", r.status_code == 302 and r.headers["Location"].endswith("/box/setup/"), r.status_code)

# 2. First run, through the real form.
page, token = csrf(s, BASE + "/box/setup/")
check("setup page opens", page.status_code == 200 and "pub" in page.text.lower(), page.status_code)
r = s.post(BASE + "/box/setup/", data={
    "csrfmiddlewaretoken": token, "name": "Edge Pub", "tenant_type": "pub", "outlet_name": "Edge Main",
    "gst_no": "", "username": "edge_owner", "password": "edge-pass-2026", "password_again": "edge-pass-2026",
}, headers={"Referer": BASE + "/box/setup/"}, allow_redirects=False)
check("setup makes the restaurant and signs the owner in", r.status_code == 302 and "/setup/onboard/" in r.headers.get("Location", ""),
      f"{r.status_code} {r.headers.get('Location')} {r.text[:300]}")

# 3. The app, from the box itself, as an Android app.
r = requests.get(BASE + "/android/rasova.apk")
check("the box serves the app", r.status_code == 200 and r.content[:2] == b"PK", r.status_code)
check("served as an Android app", r.headers.get("Content-Type") == "application/vnd.android.package-archive", r.headers.get("Content-Type"))
check("it is version 1.3.0's file (1.1 MB, not the old 1.2.0)", len(r.content) > 1_170_000, len(r.content))

# 4. Page data: http, no agent key.
html = s.get(BASE + "/setup/").text
m = re.search(r'<script id="rasova-data" type="application/json">(.*?)</script>', html, re.S)
data = json.loads(m.group(1)) if m else {}
check("pages say http://, not https", data.get("server_url") == BASE, data.get("server_url"))
check("pages hand no print agent key", "agent_key" not in data, list(data))
icon = re.search(r'rel="apple-touch-icon" sizes="180x180" href="(/static/icon-180[^"]*\.png)"', html)
check("home screen icon is the PNG", icon is not None)
if icon:
    r = requests.get(BASE + icon.group(1))
    check("and the box serves it", r.status_code == 200 and r.content[:4] == b"\x89PNG", r.status_code)
check("Setup has the Printers card", 'id="printersCard"' in html)

# 5. Connect a device: learns the number, the code carries it.
page = s.get(BASE + "/box/connect/")
host = BASE.split("//")[1]
check("Connect a device opens", page.status_code == 200, page.status_code)
check("the pairing code carries this address", f'data-qr="http://{host}/login/"' in page.text)
# Only where the box's own localhost is the box: BOX_E2E_ON_THE_BOX=1 (a PC
# running the box under Docker, or the mini PC itself).
if os.environ.get("BOX_E2E_ON_THE_BOX"):
    cookie_header = "; ".join(f"{c.name}={c.value}" for c in s.cookies)
    port = host.split(":")[1] if ":" in host else "80"
    local = requests.get(f"http://localhost:{port}/box/connect/", headers={"Cookie": cookie_header})
    check("opened on the box itself it says so, and still pairs by the learned number",
          'id="onTheBox"' in local.text and f'data-qr="http://{host}/login/"' in local.text, local.status_code)
r = s.get(BASE + "/box/connect/Rasova.cmd")
check("the PC shortcut downloads with Windows line ends",
      r.status_code == 200 and f'set "RASOVA=http://{host}/login/"' in r.text and "\r\n" in r.text, r.status_code)

# 6. Printers on a box.
page = s.get(BASE + "/setup/printers/")
check("Printers opens", page.status_code == 200, page.status_code)
check("it says the box prints by itself", "This box sends every bill and KOT" in page.text)
check("no cloud-only parts on a box", all(x not in page.text for x in ('name="agent_host"', 'name="printer_name"', "rasova.apk\" download")))
ids = re.findall(r'id="printer-(\d+)" data-station', page.text)
check("the restaurant's station is there", bool(ids), ids)
sid = ids[0]
token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text).group(1)
hdr = {"X-CSRFToken": token, "Referer": BASE + "/setup/printers/", "Content-Type": "application/json"}

r = s.post(BASE + f"/setup/printers/{sid}/save/", data=json.dumps({"printer_ip": "172.020.000.006"}), headers=hdr)
check("a zero-padded address from the self-test page is saved as the printer", r.status_code == 200 and r.json().get("printer_ip") == PRINTER,
      f"{r.status_code} {r.text[:200]}")
r = s.post(BASE + f"/setup/printers/{sid}/save/", data=json.dumps({"printer_ip": "192.168.1."}), headers=hdr)
check("a typo is a sentence, not a 500", r.status_code == 400 and "Feed button" in r.json().get("error", ""), r.status_code)

r = s.post(BASE + f"/setup/printers/{sid}/test/", headers=hdr)
job = r.json().get("job_id")
check("Test print is queued with the box's words", r.status_code == 200 and "the box prints it" in r.json().get("message", ""), r.text[:200])
status = None
for _ in range(40):
    status = s.get(BASE + f"/printing/job/{job}/", headers={"Accept": "application/json"}).json()
    if status.get("status") in ("done", "failed"):
        break
    time.sleep(0.5)
check("the box itself printed it (no phone, no agent)", status.get("status") == "done", status)

# A second station whose printer never answers.
page, token = csrf(s, BASE + "/setup/kitchen-stations/")
s.post(BASE + "/setup/kitchen-stations/", data={"csrfmiddlewaretoken": token, "station_name": "Bar"},
       headers={"Referer": BASE + "/setup/kitchen-stations/"})
page = s.get(BASE + "/setup/printers/")
bar = [i for i in re.findall(r'id="printer-(\d+)" data-station', page.text) if i != sid][0]
s.post(BASE + f"/setup/printers/{bar}/save/", data=json.dumps({"printer_ip": "172.20.0.250"}), headers=hdr)
r = s.post(BASE + f"/setup/printers/{bar}/test/", headers=hdr)
job = r.json().get("job_id")
for _ in range(80):
    status = s.get(BASE + f"/printing/job/{job}/", headers={"Accept": "application/json"}).json()
    if status.get("status") in ("done", "failed"):
        break
    time.sleep(0.5)
check("a printer that never answers fails with a reason", status.get("status") == "failed" and "didn't answer" in status.get("error", ""), status)
page = s.get(BASE + "/setup/printers/").text
check("its row says when and why", "Didn&#x27;t print at" in page or "Didn't print at" in page)
setup = s.get(BASE + "/setup/").text
check("the Setup card names the failing printer", "Bar didn" in setup)
health = s.get(BASE + "/box/health/").text
check("Box health shows the printers and links to Connect a device", 'id="printersPanel"' in health and 'href="/box/connect/"' in health)

# 7. The menu file: a box has no AI import, so a menu read in the cloud comes as a file.
menu = s.get(BASE + "/menu/").text
check("the box's menu page has no AI Import and links the menu file",
      'data-action="menu.aiImport"' not in menu and 'id="menuFileLink"' in menu)
menu_file = {
    "format": "rasova-menu", "version": 1,
    "stations": [{"name": "Kitchen"}, {"name": "Bar", "made_on_send": True}],
    "vat_classes": [{"name": "IMFL", "rate": "35.00"}],
    "modifier_groups": [{"name": "Spice", "modifiers": [{"name": "Extra Hot", "price": "0"}]}],
    "categories": [
        {"name": "Starters", "items": [{"name": "Copper Tap Wings", "price": "280", "gst_percentage": "5.00",
                                        "station": "Kitchen", "modifier_groups": ["Spice"]}]},
        {"name": "Drinks", "items": [{"name": "Copper Tap Single Malt", "price": "450", "gst_percentage": "0.00",
                                      "vat_class": "IMFL", "station": "Bar"}]},
    ],
}
page = s.get(BASE + "/menu/file/")
token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text).group(1)
r = s.post(BASE + "/menu/file/check/", files={"menu_file": ("menu.json", json.dumps(menu_file), "application/json")},
           data={"csrfmiddlewaretoken": token}, headers={"Referer": BASE + "/menu/file/"})
check("a menu file is checked first, nothing changed yet", 'id="newItems">2<' in r.text and "Copper Tap Wings" not in s.get(BASE + "/menu/").text,
      r.status_code)
load_token = re.search(r'name="token" value="([^"]+)"', r.text)
if load_token:
    s.post(BASE + "/menu/file/load/", data={"csrfmiddlewaretoken": token, "token": load_token.group(1)},
           headers={"Referer": BASE + "/menu/file/"})
menu = s.get(BASE + "/menu/").text
check("then loaded: the dishes are on the box's menu", "Copper Tap Wings" in menu and "Copper Tap Single Malt" in menu)

# 8. Updates from a pen drive: the panel is there for the owner.
health = s.get(BASE + "/box/health/").text
check("Box health has the pen drive update", 'id="updatePanel"' in health and 'id="lookForUpdate"' in health)

# 9. The old address and the agent endpoints.
r = s.get(BASE + "/setup/printer/", allow_redirects=False)
check("the old printer page redirects", r.status_code == 301 and r.headers["Location"] == "/setup/printers/", r.status_code)

print()
failed = [n for n, ok in results if not ok]
print(f"{len(results) - len(failed)} of {len(results)} passed")
sys.exit(1 if failed else 0)
