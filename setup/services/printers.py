"""
Every printer of an outlet, in one place (Setup > Printers, 22 Nov 2026).

A printer is a kitchen station's printer: the default station's is the
counter printer (bills, and every KOT in One Printer mode), the others
print their own KOTs. What a printer is set to lives on its KitchenStation;
the settings for every printer live on the outlet and its PrintProfile.
This module is the one place that reads what an owner typed for a printer,
says how each printer is doing, and makes the test page.

How a printer is doing comes from the print queue itself (printing.PrintJob):
each job carries the printer's address, so the last page that printed and
the last one that failed, with the reason, are facts, not guesses. Two
queries however many printers there are.
"""
import base64
import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_ipv46_address
from django.db.models import Max
from django.db.models.fields.json import KT
from django.utils import timezone

from setup.models import KitchenStation

# How far back the status looks: a printer quiet for a week says "no
# prints yet" rather than reading a month of the queue.
STATUS_WINDOW = timedelta(days=7)

PAPERS = {58, 80}
CUTS = {c for c, _ in KitchenStation.CUT_CHOICES}
ENCODINGS = {e for e, _ in KitchenStation.ENCODING_CHOICES}
MAC = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
MARGINS = (0, 2, 4, 6, 8)


class PrinterInputError(ValueError):
    """A sentence for the owner: what was wrong and what to type."""


def read_printer(data, station):
    """What the owner typed for one printer, checked. Returns the fields to
    save on the station (and "printer_mac" for the counter printer).

    Before this, a typo such as "192.168.1." went straight to the database,
    whose inet column refused it with a 500, and a port of 0 or 99999 was
    saved and could never print."""
    out = {}
    ip = str(data.get("printer_ip") or "").strip()
    if ip:
        try:
            validate_ipv46_address(ip)
        except ValidationError:
            raise PrinterInputError(f"\"{ip[:40]}\" is not a printer address. It looks like 192.168.1.60: "
                                    "hold the printer's Feed button to print its own address.")
    out["printer_ip"] = ip or None

    port = str(data.get("printer_port") or "9100").strip()
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise PrinterInputError("The port is a number from 1 to 65535. Thermal printers almost always use 9100.")
    out["printer_port"] = int(port)

    paper = str(data.get("paper_width_mm") or "80").strip()
    if not paper.isdigit() or int(paper) not in PAPERS:
        raise PrinterInputError("The paper is 80 mm or 58 mm wide.")
    out["paper_width_mm"] = int(paper)

    cut = str(data.get("cut_type") or "full").strip()
    if cut not in CUTS:
        raise PrinterInputError("Choose a full cut, a partial cut or no cut.")
    out["cut_type"] = cut

    encoding = str(data.get("printer_encoding") or "cp437").strip()
    if encoding not in ENCODINGS:
        raise PrinterInputError("Choose CP437, CP850 or UTF-8.")
    out["printer_encoding"] = encoding

    # The Windows printer name, for USB printing through QZ Tray. A box has
    # no QZ Tray: what it had is kept, never cleared by a form that lacks it.
    if "printer_name" in data:
        out["printer_name"] = str(data.get("printer_name") or "").strip()[:200]

    if station.is_default and "printer_mac" in data:
        mac = re.sub(r"[^0-9A-F]", "", str(data.get("printer_mac") or "").upper())
        mac = ":".join(mac[i:i + 2] for i in range(0, len(mac), 2))
        if mac and not MAC.match(mac):
            raise PrinterInputError("A MAC address has 12 letters and digits, like 00:57:27:10:ED:46. "
                                    "Leave it blank if you don't have it.")
        out["printer_mac"] = mac
    return out


def save_printer(station, data):
    """Check and save one printer. The counter printer's paper width and MAC
    are also the outlet's (the bill reads them there)."""
    fields = read_printer(data, station)
    mac = fields.pop("printer_mac", None)
    for name, value in fields.items():
        setattr(station, name, value)
    station.save(update_fields=list(fields))
    if station.is_default:
        outlet = station.outlet
        outlet.paper_width_mm = station.paper_width_mm
        update = ["paper_width_mm"]
        if mac is not None:
            outlet.printer_mac = mac
            update.append("printer_mac")
        outlet.save(update_fields=update)
    return station


def printer_states(outlet, stations, now=None):
    """{station id: state} for the outlet's stations. A state is a dict:
    level "ok", "bad", "idle" or "none" (no printer set), the words to show,
    and the facts behind them. Two queries, however many printers."""
    from printing.models import PrintJob

    now = now or timezone.now()
    hosts = sorted({s.printer_ip for s in stations if s.printer_ip})
    last_ok, last_fail = {}, {}
    if hosts:
        recent = (PrintJob.objects.for_outlet(outlet.tenant_id, outlet.pk)
                  .filter(done_at__gte=now - STATUS_WINDOW)
                  .annotate(host=KT("payload__network_host"), port=KT("payload__network_port"))
                  .filter(host__in=hosts))
        for row in (recent.filter(status=PrintJob.DONE).values("host", "port")
                    .annotate(last=Max("done_at"))):
            key = (row["host"], str(row["port"] or 9100))
            last_ok[key] = max(row["last"], last_ok.get(key, row["last"]))
        for row in (recent.filter(status=PrintJob.FAILED).order_by("host", "port", "-done_at")
                    .distinct("host", "port").values("host", "port", "done_at", "error_msg")):
            key = (row["host"], str(row["port"] or 9100))      # an old job with no port is a 9100 one
            if key not in last_fail or row["done_at"] > last_fail[key]["done_at"]:
                last_fail[key] = row

    states = {}
    for station in stations:
        if not station.printer_ip:
            states[station.id] = {"level": "none", "words": "No printer set"}
            continue
        key = (station.printer_ip, str(station.printer_port))
        ok, fail = last_ok.get(key), last_fail.get(key)
        if fail and (ok is None or fail["done_at"] > ok):
            when = timezone.localtime(fail["done_at"]).strftime("%H:%M")
            states[station.id] = {"level": "bad", "at": fail["done_at"], "error": fail["error_msg"],
                                  "words": f"Didn't print at {when}: {fail['error_msg'] or 'no reason given'}"}
        elif ok:
            states[station.id] = {"level": "ok", "at": ok, "words": f"Printed {_ago(now - ok)}"}
        else:
            states[station.id] = {"level": "idle", "words": "No prints in the last week"}
    return states


def _ago(delta):
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 24 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def outlet_stations(outlet):
    """The outlet's stations, counter printer first."""
    return list(KitchenStation.objects.filter(tenant_id=outlet.tenant_id, outlet=outlet)
                .order_by("-is_default", "name", "id"))


def printers_summary(outlet):
    """One line for the Setup card and Box health: "3 printers, all OK",
    "Bar didn't print at 20:12", or "No printer yet". level as in
    printer_states."""
    stations = outlet_stations(outlet)
    with_printer = [s for s in stations if s.printer_ip]
    if not with_printer:
        return {"level": "none", "words": "No printer yet", "count": 0}
    states = printer_states(outlet, with_printer)
    failing = [s for s in with_printer if states[s.id]["level"] == "bad"]
    if failing:
        first = failing[0]
        when = timezone.localtime(states[first.id]["at"]).strftime("%H:%M")
        more = f" (and {len(failing) - 1} more)" if len(failing) > 1 else ""
        return {"level": "bad", "words": f"{first.name} didn't print at {when}{more}", "count": len(with_printer)}
    count = len(with_printer)
    return {"level": "ok", "words": f"{count} printer{'s' if count != 1 else ''}, none failing", "count": count}


def queue_test_print(station):
    """Put a test page for this printer on the print queue, exactly like a
    real bill: in the cloud the phone app or the PC agent collects it, on a
    box the box prints it (box/print_queue.py). The old Kitchen Stations
    test print talked to a PC agent on the device itself, so on a box, or on
    a phone, it could never work."""
    from printing.models import PrintJob

    return PrintJob.objects.create(
        tenant_id=station.tenant_id,
        outlet_id=station.outlet_id,
        payload={
            "data_b64": base64.b64encode(test_page(station)).decode("ascii"),
            "network_host": station.printer_ip,
            "network_port": station.printer_port,
            "encoding": station.printer_encoding,
        },
    )


def test_print_message():
    return ("Test sent: the box prints it in a few seconds." if getattr(settings, "BOX", False)
            else "Test queued: a receipt prints in a few seconds if your phone app is open and logged in.")


def test_page(station) -> bytes:
    """Raw ESC/POS bytes for a "RASOVA TEST PRINT" page, sized to the
    printer, naming the station so a pub with three printers knows which
    one answered. Plain ASCII: the printer's code page has no dashes."""
    esc, gs = b"\x1b", b"\x1d"
    enc = station.printer_encoding or "cp437"
    chars = station.chars_per_line

    def text(words):
        return words.encode("ascii", errors="replace").decode("ascii").encode(enc, errors="replace")

    line = text("-" * chars + "\n")
    cut = {"full": gs + b"V\x00", "partial": gs + b"V\x01", "none": b""}.get(station.cut_type, gs + b"V\x00")
    buf = esc + b"@"                                 # init
    buf += esc + b"a\x01"                            # centre
    buf += gs + b"!\x11"                             # double width and height
    buf += text("RASOVA\n")
    buf += gs + b"!\x00"
    buf += text("TEST PRINT\n")
    buf += text(f"{station.name}\n"[:chars + 1])
    buf += line
    buf += esc + b"a\x00"                            # left
    buf += text(f"Printer IP : {station.printer_ip}\n")
    buf += text(f"Port       : {station.printer_port}\n")
    buf += text(f"Paper      : {station.paper_width_mm}mm ({chars} chars)\n")
    buf += line
    buf += esc + b"a\x01"
    buf += text("If you can read this,\nprinting works!\n")
    buf += b"\n\n\n\n\n\n"                           # past the cutter
    return buf + cut
