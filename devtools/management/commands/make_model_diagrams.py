"""
Draw Rasova's database diagrams from the models themselves, into
docs/diagrams/ (so they can't drift from the code the way hand-drawn ones do).

    python manage.py make_model_diagrams              # .dot, .png and .svg for every diagram
    python manage.py make_model_diagrams --dot-only   # just the .dot files (no Graphviz needed)

Needs Graphviz's `dot` for the pictures (on Windows it's usually in
C:\\Program Files\\Graphviz\\bin). What it draws:

  schema.dot, pos_er_diagram     every model, Django's own and third-party included
  pos_grouped                    every model, boxed by app
  pos_core_schema                the restaurant's core: tenants, staff, menu, orders,
                                 kitchen, payments, printing, tokens, tables
  pos_lr, pos_rl                 the core again, drawn left to right and right to left
  apps/<app>                     one per Rasova app: its models in full, and the
                                 models it points at as small labels
"""
import shutil
import subprocess
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

CORE_APPS = ["tenants", "accounts", "menu", "orders", "kitchen", "payments", "printing", "tokens",
             "tablemerge", "waiter"]
DOT_PATHS = [r"C:\Program Files\Graphviz\bin\dot.exe", "/usr/bin/dot", "/usr/local/bin/dot", "/opt/homebrew/bin/dot"]


def rasova_apps():
    """Rasova's own apps that have models, in INSTALLED_APPS order."""
    base = Path(settings.BASE_DIR)
    out = []
    for cfg in apps.get_app_configs():
        path = Path(cfg.path)
        if path.is_relative_to(base) and "site-packages" not in path.parts and list(cfg.get_models()):
            out.append(cfg.label)
    return out


def find_dot():
    found = shutil.which("dot")
    if found:
        return found
    for candidate in DOT_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def shrink_png(path):
    """The diagrams are black, white and one green: a 32-colour palette keeps
    them as sharp at about a third of the size, so the repository doesn't
    grow by megabytes every time they're redrawn."""
    from PIL import Image
    with Image.open(path) as image:
        small = image.convert("RGB").quantize(colors=32, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    small.save(path, "PNG", optimize=True)


class Command(BaseCommand):
    help = "Draw the model diagrams in docs/diagrams/ from the current models."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(Path(settings.BASE_DIR) / "docs" / "diagrams"))
        parser.add_argument("--dot-only", action="store_true", help="Write the .dot files only.")

    def handle(self, *args, out, dot_only, **options):
        out = Path(out)
        (out / "apps").mkdir(parents=True, exist_ok=True)
        ours = rasova_apps()
        plans = [
            ("schema", [], {"all_applications": True}),
            ("pos_er_diagram", [], {"all_applications": True}),
            ("pos_grouped", [], {"all_applications": True, "group_models": True}),
            ("pos_core_schema", CORE_APPS, {}),
            ("pos_lr", CORE_APPS, {"rankdir": "LR"}),
            ("pos_rl", CORE_APPS, {"rankdir": "RL"}),
        ] + [(f"apps/{label}", [label], {}) for label in ours]

        dot = None if dot_only else find_dot()
        if not dot_only and not dot:
            raise CommandError("Graphviz's dot isn't installed (or isn't on PATH). Use --dot-only, or install Graphviz.")
        for name, labels, options_ in plans:
            dot_file = out / f"{name}.dot"
            # TenantScopedModel is left out: nearly every model inherits it, and its
            # dozens of "abstract inheritance" arrows hid the real relations.
            call_command("graph_models", *labels, dot=True, output=str(dot_file),
                         exclude_models="TenantScopedModel", **options_)
            if name == "schema" or dot_only:
                continue
            for fmt in ("png", "svg"):
                made = subprocess.run([dot, f"-T{fmt}", str(dot_file), "-o", str(out / f"{name}.{fmt}")],
                                      capture_output=True, text=True)
                if made.returncode != 0:
                    raise CommandError(f"dot failed on {name}.{fmt}: {made.stderr.strip()[:300]}")
            shrink_png(out / f"{name}.png")
            dot_file.unlink()                       # the pictures are kept; schema.dot is the one source kept
        self.stdout.write(f"Drew {len(plans) - 1} diagrams of {len(ours)} apps into {out}")
