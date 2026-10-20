import shutil
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase

from devtools.management.commands.make_model_diagrams import rasova_apps


class MakeModelDiagramsTest(SimpleTestCase):
    """The diagrams are drawn from the models, so a new app or model can't be left out."""

    def test_every_app_gets_a_diagram_and_the_full_one_has_every_model(self):
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        call_command("make_model_diagrams", out=str(out), dot_only=True, stdout=open(out / "log.txt", "w"))

        labels = rasova_apps()
        self.assertIn("box", labels)
        self.assertIn("covers", labels)
        for label in labels:
            self.assertTrue((out / "apps" / f"{label}.dot").exists(), label)

        full = (out / "schema.dot").read_text(encoding="utf-8")
        for node in ("orders_models_Order", "covers_models_Cover", "inventory_models_BarCount",
                     "inventory_models_KegChange", "box_models_BoxState", "printing_models_PrintJob", "offers_models_Offer"):
            self.assertIn(f"{node} [label=", full, node)
        # the base nearly every model inherits is left out, so its arrows don't bury the real ones
        self.assertNotIn("core_models_TenantScopedModel", full)
