"""
Make a signed update bundle for Rasova boxes. Runs on Rasova's PC only:
needs Docker and the update key in ~/.rasova_box/update_signing_key.pem,
which never goes on a box.

    docker build -f box/docker/Dockerfile --target box -t rasova-box:1.4.0 --build-arg RASOVA_VERSION=1.4.0 .
    python manage.py make_box_bundle --release 1.4.0 --out F:\bundles\rasova-box-1.4.0

The bundle carries every image compose.yml names (Rasova, Postgres, Redis):
a box never downloads anything, so the first install and every update bring
all they need.
"""
import gzip
import json
import re
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

COMPOSE = Path(settings.BASE_DIR) / "box" / "docker" / "compose.yml"


def images_in(compose_text, version):
    names = re.findall(r"^\s+image:\s*(\S+)\s*$", compose_text, flags=re.M)
    return sorted({n.replace("${RASOVA_VERSION:-latest}", version) for n in names})


class Command(BaseCommand):
    help = "Make a signed Rasova box update bundle (Rasova's PC only)."

    def add_arguments(self, parser):
        parser.add_argument("--release", required=True, help="The version, e.g. 1.4.0 (--version is Django's own).")
        parser.add_argument("--out", required=True)
        parser.add_argument("--key", default=str(Path.home() / ".rasova_box" / "update_signing_key.pem"))

    def handle(self, *args, release, out, key, **options):
        version = release
        from box.updates import sha256_of, sign_manifest, verify_bundle
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,40}", version):
            raise CommandError("--release: letters, digits, dots and dashes only (it becomes the image tag).")
        try:
            private_pem = Path(key).read_bytes()
        except OSError:
            raise CommandError(f"No update key at {key}.")
        compose = COMPOSE.read_text(encoding="utf-8")
        images = images_in(compose, version)
        for image in images:
            if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
                raise CommandError(f"{image} isn't on this PC: build it (Rasova) or docker pull it (the others) first.")

        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        tar = out / "images.tar"
        made = subprocess.run(["docker", "save", "-o", str(tar), *images], capture_output=True, text=True)
        if made.returncode != 0:
            raise CommandError(f"docker save failed: {made.stderr.strip()}")
        with open(tar, "rb") as src, gzip.open(out / "images.tar.gz", "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
        tar.unlink()
        (out / "compose.yml").write_bytes(compose.encode("utf-8"))
        manifest = json.dumps({"version": version, "images": images, "files": {
            name: sha256_of(out / name) for name in ("images.tar.gz", "compose.yml")}}, indent=1, sort_keys=True).encode()
        (out / "manifest.json").write_bytes(manifest)
        (out / "manifest.sig").write_text(sign_manifest(manifest, private_pem))
        verify_bundle(out)
        self.stdout.write(f"Bundle {version} ({', '.join(images)}) written to {out}")
