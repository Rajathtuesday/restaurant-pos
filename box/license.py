"""
The box's licence: a small file signed by Rasova, checked by the box itself,
so it works with no internet.

    {"payload": {"box_id": "...", "customer": "Copper Tap", "expires": "2027-10-31",
                 "issued": "2026-10-20", "edition": "box"},
     "signature": "<base64 Ed25519 over the payload's canonical JSON>"}

Only Rasova's private key (kept outside the repository, used by
`python manage.py sign_box_license`) can make a signature that the public
key below accepts, so a licence can't be edited or forged. box_id is a hash
of the computer's machine id: a licence copied to another box doesn't fit.

States:
  valid      before the end date
  grace      up to GRACE_DAYS after it: everything works, a warning shows
  expired    after the grace: open bills can still be billed and paid,
             reports and exports still work, but no new order can start
  trial      no licence yet: TRIAL_DAYS from the first switch-on
  invalid    a file that isn't a real, unedited Rasova licence
  moved      a real licence for a different box, found here: this box
             replaced one that died, and its backup (which carries the
             licence) was restored here. Everything works for GRACE_DAYS
             from when this box first saw it, so the replacement opens that
             night; the banner asks for this box's own licence.
  wrong_box  after that, or a licence copied from a box that is still alive
Never cuts off an order in the middle of a service (box/signals.py only
refuses to START a new order).
"""
import base64
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

LICENSE_PUBLIC_KEY_B64 = "6MZnzqXu5odtMFGDaUgwk8LNy/suYOzQiaZy9B8Gzos="
GRACE_DAYS = 14
TRIAL_DAYS = 14
CACHE_SECONDS = 300

BLOCKING = ("expired", "invalid", "wrong_box", "trial_over")


@dataclass(frozen=True)
class LicenseState:
    status: str
    customer: str = ""
    expires: date | None = None
    days_left: int | None = None

    @property
    def blocks_new_orders(self):
        return self.status in BLOCKING

    @property
    def message(self):
        if self.status == "valid":
            return f"Licensed to {self.customer} until {self.expires:%d %b %Y}."
        if self.status == "grace":
            return (f"Rasova's licence ended on {self.expires:%d %b %Y}. Everything still works for "
                    f"{self.days_left} more day{'s' if self.days_left != 1 else ''}; renew it before then.")
        if self.status == "expired":
            return (f"Rasova's licence ended on {self.expires:%d %b %Y}. Open bills can be finished and "
                    "paid, but new orders can't start until it is renewed.")
        if self.status == "trial":
            return f"Trial: {self.days_left} day{'s' if self.days_left != 1 else ''} left. Add the licence file to keep using it."
        if self.status == "trial_over":
            return "The trial has ended. Add the licence file to start new orders."
        if self.status == "moved":
            return (f"This licence was made for another Rasova box. Everything works for {self.days_left} more "
                    f"day{'s' if self.days_left != 1 else ''}: send Rasova this box's id (Box health) for its own licence.")
        if self.status == "wrong_box":
            return "This licence is for a different box. Ask Rasova for this box's licence."
        return "The licence file isn't valid. Ask Rasova for a new one."


def machine_id():
    """A short, stable id for this computer (a hash of its machine id)."""
    path = Path(getattr(settings, "BOX_MACHINE_ID_FILE", "/etc/machine-id"))
    try:
        raw = path.read_text().strip()
    except OSError:
        raw = ""
    if not raw:
        return "unknown"
    return hashlib.sha256(("rasova-box:" + raw).encode()).hexdigest()[:16]


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def verify_signature(payload, signature_b64, public_key_b64=None):
    """True if Rasova's key signed exactly this payload. The key is read at
    call time (not fixed when the module loads), so it can be rotated."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64 or LICENSE_PUBLIC_KEY_B64))
        key.verify(base64.b64decode(signature_b64), canonical(payload))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def license_path():
    return Path(getattr(settings, "BOX_LICENSE_FILE", "/data/license.json"))


def evaluate(document, today=None, box_id=None, setup_done=None, moved_since=None):
    """The state of a licence document (a dict, or None for no file).
    moved_since: when this box first saw a real licence for another box."""
    today = today or timezone.localdate()
    if document is None:
        if setup_done is None:
            return LicenseState("trial", days_left=TRIAL_DAYS)
        end = timezone.localdate(setup_done) + timedelta(days=TRIAL_DAYS)
        left = (end - today).days
        return LicenseState("trial", days_left=left) if left >= 0 else LicenseState("trial_over", days_left=0)
    try:
        payload, signature = document["payload"], document["signature"]
        expires = date.fromisoformat(payload["expires"])
        customer = str(payload.get("customer", ""))
    except (KeyError, TypeError, ValueError):
        return LicenseState("invalid")
    if not verify_signature(payload, signature):
        return LicenseState("invalid")
    if payload.get("box_id") != (box_id or machine_id()):
        if moved_since is not None and today <= expires + timedelta(days=GRACE_DAYS):
            left = (timezone.localdate(moved_since) + timedelta(days=GRACE_DAYS) - today).days
            if left >= 0:
                return LicenseState("moved", customer, expires, left)
        return LicenseState("wrong_box", customer=customer, expires=expires)
    if today <= expires:
        return LicenseState("valid", customer, expires, (expires - today).days)
    grace_left = (expires + timedelta(days=GRACE_DAYS) - today).days
    if grace_left >= 0:
        return LicenseState("grace", customer, expires, grace_left)
    return LicenseState("expired", customer, expires, 0)


def read_document(path=None):
    path = path or license_path()
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"broken": True}


_cache = {"at": 0.0, "state": None}


def current(refresh=False):
    """This box's licence state, re-read at most every CACHE_SECONDS."""
    if not refresh and _cache["state"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
        return _cache["state"]
    from box.models import BoxState
    box = BoxState.get()
    document = read_document()
    state = evaluate(document, setup_done=box.setup_done_at, moved_since=box.licence_moved_at)
    if state.status == "wrong_box" and box.licence_moved_at is None:
        # First sight of another box's licence here: the grace starts now
        # (once: the filter makes two processes agree on the same moment).
        BoxState.objects.filter(pk=1, licence_moved_at__isnull=True).update(licence_moved_at=timezone.now())
        state = evaluate(document, setup_done=box.setup_done_at,
                         moved_since=BoxState.objects.get(pk=1).licence_moved_at)
    _cache.update(at=time.monotonic(), state=state)
    return state


def install(document_text):
    """Check a licence file someone uploaded and, if it fits this box, save
    it. Returns the new state; LicenseState('invalid') changes nothing."""
    try:
        document = json.loads(document_text)
    except ValueError:
        return LicenseState("invalid")
    state = evaluate(document)
    if state.status in ("invalid", "wrong_box"):
        return state
    _write(document, license_path())
    from box.models import BoxState
    BoxState.get()
    BoxState.objects.filter(pk=1).update(licence_moved_at=None)    # its own licence now
    _cache["state"] = None
    return current(refresh=True)


def adopt(path):
    """After a restore onto a box with no licence: take the one the backups
    carry (backup.run_backup copies it there), even if it was made for the
    box that died; current() then gives it the 'moved' grace. Only a real,
    unedited Rasova licence is taken. Returns True if one was taken."""
    if license_path().exists():
        return False
    document = read_document(path)
    if not document or evaluate(document).status == "invalid":
        return False
    _write(document, license_path())
    _cache["state"] = None
    return True


def _write(document, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(document, indent=1), encoding="utf-8")
    tmp.replace(path)                       # a power cut never leaves half a file
