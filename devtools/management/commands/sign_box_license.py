"""
Make a licence file for one Rasova box. Runs on Rasova's own PC only: it
needs the private key in ~/.rasova_box/license_signing_key.pem, which never
goes in the repository or on a box.

    python manage.py sign_box_license --box-id 3f2a... --customer "Copper Tap" --expires 2027-10-31 --out copper_tap.json
"""
import base64
import json
import re
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Sign a licence file for a Rasova box (Rasova's PC only)."

    def add_arguments(self, parser):
        parser.add_argument("--box-id", required=True, help="From the box's health page.")
        parser.add_argument("--customer", required=True)
        parser.add_argument("--expires", required=True, help="YYYY-MM-DD")
        parser.add_argument("--out", required=True)
        parser.add_argument("--key", default=str(Path.home() / ".rasova_box" / "license_signing_key.pem"))

    def handle(self, *args, box_id, customer, expires, out, key, **options):
        from cryptography.hazmat.primitives import serialization

        from box.license import canonical, verify_signature
        if not re.fullmatch(r"[0-9a-f]{16}", box_id.strip()):
            # "unknown" is what a box with no machine id shows: a licence for it would fit every such box
            raise CommandError("--box-id must be the 16 letters and digits on the box's health page.")
        try:
            date.fromisoformat(expires)
        except ValueError:
            raise CommandError("--expires must be YYYY-MM-DD.")
        try:
            private = serialization.load_pem_private_key(Path(key).read_bytes(), password=None)
        except OSError:
            raise CommandError(f"No signing key at {key}.")
        payload = {"box_id": box_id.strip(), "customer": customer.strip(), "expires": expires,
                   "issued": date.today().isoformat(), "edition": "box"}
        signature = base64.b64encode(private.sign(canonical(payload))).decode()
        if not verify_signature(payload, signature):
            raise CommandError("The key doesn't match the box's public key: wrong key file?")
        Path(out).write_text(json.dumps({"payload": payload, "signature": signature}, indent=1), encoding="utf-8")
        self.stdout.write(f"Licence for {customer} until {expires} written to {out}")
