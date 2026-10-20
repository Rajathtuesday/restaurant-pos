"""
Updates for a box come as a bundle folder (on a pen drive, or downloaded):

    manifest.json      {"version": "1.4.0", "files": {"images.tar.gz": "<sha256>", "compose.yml": "<sha256>"}}
    manifest.sig       Ed25519 signature of manifest.json, by Rasova's update key
    images.tar.gz      the new image
    compose.yml        how the box runs it

The box checks the bundle with the version it is ALREADY running (trusted),
before loading anything: the signature must be Rasova's, and every file must
match its hash. A bundle that was edited, damaged on the pen drive, or made
by anyone else is refused, and nothing changes. (rasova-box update runs the
check, then backs up, installs, checks the new version's health, and goes
back to the old version and the backup if that fails.)
"""
import base64
import hashlib
import json
from pathlib import Path

UPDATE_PUBLIC_KEY_B64 = "7uU/qF97gCPov89liw/kJYj+bV51XeqVhdC2HKrF4UI="


class BundleError(Exception):
    pass


def sha256_of(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify_bundle(folder, public_key_b64=None):
    """The bundle's manifest if it is Rasova's and complete; BundleError otherwise."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    folder = Path(folder)
    try:
        manifest_bytes = (folder / "manifest.json").read_bytes()
        signature = base64.b64decode((folder / "manifest.sig").read_text().strip())
    except (OSError, ValueError):
        raise BundleError("The update is missing its manifest or signature.")
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64 or UPDATE_PUBLIC_KEY_B64))
        key.verify(signature, manifest_bytes)
    except (InvalidSignature, ValueError):
        raise BundleError("The update is not signed by Rasova. Nothing was changed.")
    try:
        manifest = json.loads(manifest_bytes)
        files = manifest["files"]
        version = str(manifest["version"])
    except (ValueError, KeyError, TypeError):
        raise BundleError("The update's manifest is broken.")
    for name, expected in files.items():
        if "/" in name or "\\" in name or name.startswith("."):
            raise BundleError(f"The update names an odd file: {name}")
        path = folder / name
        if not path.exists():
            raise BundleError(f"The update is missing {name}.")
        if sha256_of(path) != expected:
            raise BundleError(f"{name} is damaged or changed (its fingerprint doesn't match).")
    return {"version": version, "files": files}


def sign_manifest(manifest_bytes, private_key_pem):
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    return base64.b64encode(key.sign(manifest_bytes)).decode()
