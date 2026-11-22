"""
Box health's "Update from a pen drive" (22 Nov 2026): the web app's half.

The web app runs in a container with no say over Docker, so it can't update
Rasova itself. It writes a request (a few key=value lines) into the box's
data folder; on the box, a systemd path unit runs `rasova-box usb-request`
(box/host/rasova-box), which reads the pen drive, checks every bundle's
signature, installs with the same steps as `rasova-box update` (backup,
refusal while bills are open, health check, going back by itself) and writes
what happened to data/update-status. This module writes requests and reads
that status, strictly: the status is shown on a page, so only known keys,
known states and version-shaped versions get through.
"""
import os
import re
import secrets
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

STATES = {"scanning", "found", "none", "installing", "done", "failed"}
WORKING = {"asked", "scanning", "installing"}
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,40}$")
# A request nobody picked up in this long: the box has no helper (an older box,
# or `rasova-box enable-usb-updates` was never run) or it isn't running.
NO_ANSWER_AFTER = 60


def data_dir():
    """The box's data folder (/data in the container, /srv/rasova/data on the
    box): where the licence lives, shared with the host."""
    return Path(settings.BOX_LICENSE_FILE).parent


def requests_dir():
    return data_dir() / "update-requests"


def status_file():
    return data_dir() / "update-status"


def ask(action, version=""):
    """Write a request for the box's helper. Returns its id. The file is
    written beside the folder and moved in, so the helper (woken as soon as
    the folder isn't empty) never reads half a request."""
    if action not in ("scan", "install"):
        raise ValueError(action)
    if action == "install" and not VERSION.match(version or ""):
        raise ValueError(version)
    folder = requests_dir()
    folder.mkdir(parents=True, exist_ok=True)
    request_id = secrets.token_hex(12)
    body = f"id={request_id}\naction={action}\n" + (f"version={version}\n" if action == "install" else "")
    tmp = data_dir() / f".update-request-{request_id}"
    tmp.write_text(body, encoding="ascii")
    os.replace(tmp, folder / f"{request_id}.req")
    (data_dir() / "update-asked").write_text(f"{request_id}\n{timezone.now().isoformat()}\n", encoding="ascii")
    return request_id


def read_status():
    """What the helper last said, plus whether a request is waiting for it.
    {} when nothing was ever asked."""
    status = {"found": []}
    try:
        lines = status_file().read_text(encoding="utf-8", errors="replace").splitlines()[:40]
    except OSError:
        lines = []
    for line in lines:
        key, _, value = line.partition("=")
        if key == "found":
            if VERSION.match(value) and value not in status["found"]:
                status["found"].append(value)
        elif key in ("id", "state", "at", "running", "message"):
            status[key] = value.strip()[:300]
    if status.get("state") not in STATES:
        status.pop("state", None)
    if status.get("running") and not VERSION.match(status["running"]):
        status.pop("running")
    if "at" in status:
        status["at"] = parse_datetime(status["at"]) if _looks_like_time(status["at"]) else None

    asked = _last_asked()
    if asked and asked[0] != status.get("id"):
        # A request the helper hasn't answered yet.
        waited = (timezone.now() - asked[1]).total_seconds() if asked[1] else 0
        if waited > NO_ANSWER_AFTER:
            status.update(state="failed", message="The box didn't answer. It needs its update helper: "
                                                  "ask Rasova to run rasova-box enable-usb-updates once.")
        else:
            status.update(state="asked", message="Asking the box...")
    if "state" not in status:
        return {}
    status["working"] = status["state"] in WORKING
    status["newer"] = [v for v in status["found"] if v != status.get("running")]
    return status


def _last_asked():
    try:
        request_id, at = (data_dir() / "update-asked").read_text(encoding="ascii").split("\n")[:2]
    except (OSError, ValueError):
        return None
    return request_id.strip(), parse_datetime(at.strip()) if _looks_like_time(at.strip()) else None


def _looks_like_time(text):
    return bool(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", text or ""))
