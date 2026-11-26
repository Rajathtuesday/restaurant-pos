"""
Words in Rasova's colours can be read, in light mode and in dark.

Why (26 Nov 2026, item 10 of md_files/rasova_fault_fix_plan_2026-11-26.md,
the owner's pick D3): gold #C5A059 words on white were 2.46:1, where
reading needs 4.5:1 (prices, active tabs, small labels, the dashboard's
sales figure). So were white words on a gold button, and the green and
orange status words. The brand gold stays for lines, borders and
backgrounds; words use their own tokens (static/css/themes/luxury.css):
--gold-text #8A6A2E, --success-text, --warning-text, and --on-gold,
near-black, for words on gold. Dark mode keeps its colours.

The preview he picked from: md_files/gold_text_preview_2026-11-26.html.

Run: python manage.py test core.tests.test_theme_contrast
"""
import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.testing import not_in_compiled_build

ROOT = Path(settings.BASE_DIR)
THEME = ROOT / "static" / "css" / "themes" / "luxury.css"
LIGHT_BACKGROUNDS = ("#ffffff", "#fafafa")
DARK_BACKGROUNDS = ("#000000", "#0a0a0a")
WORDS = ("--text-main", "--text-muted", "--gold-text", "--success-text", "--warning-text")


def luminance(colour):
    colour = colour.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        v = int(colour[i:i + 2], 16) / 255
        channels.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def tokens(block_selector):
    """The theme's tokens in its first block for `block_selector`, hex only."""
    css = THEME.read_text(encoding="utf-8")
    block = re.search(re.escape(block_selector) + r"\s*\{([^}]*)\}", css).group(1)
    return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b", block))


class ThemeTokensTest(SimpleTestCase):

    def test_every_word_colour_reads_in_light_mode(self):
        light = tokens(":root")
        for name in WORDS:
            for background in LIGHT_BACKGROUNDS:
                self.assertGreaterEqual(contrast(light[name], background), 4.5, f"{name} on {background}")

    def test_every_word_colour_reads_in_dark_mode(self):
        light, dark = tokens(":root"), tokens("html.dark")
        for name in WORDS:
            for background in DARK_BACKGROUNDS:
                self.assertGreaterEqual(contrast(dark.get(name, light[name]), background), 4.5, f"{name} on {background}")

    def test_words_on_a_gold_button_read_in_both_modes(self):
        light, dark = tokens(":root"), tokens("html.dark")
        self.assertGreaterEqual(contrast(light["--on-gold"], light["--accent-gold"]), 4.5)
        self.assertGreaterEqual(contrast(dark.get("--on-gold", light["--on-gold"]), dark["--accent-gold"]), 4.5)


@not_in_compiled_build("reads the stylesheets and templates from the repository")
class NoFaintWordsTest(SimpleTestCase):
    """No style colours words with the brand gold or the old green and
    orange, or puts white words on gold. Pages with their own always-dark
    palette (the marketing site, the printed bill, the token display board,
    the demo picker, the login page's dark left panel) keep theirs."""

    FAINT = re.compile(r"(?<![-\w])color\s*:\s*(var\(--accent-gold\)|#10b981|var\(--success\)|#f97316)", re.I)

    def files(self):
        out = subprocess.run(["git", "ls-files", "*.css", "*.html"], capture_output=True, text=True, cwd=ROOT).stdout.split()
        return [f for f in out if not f.startswith(("md_files/", "static/vendor/", "public/"))
                and f != "static/css/luxury.css"]       # an old copy nothing loads

    def test_no_word_is_the_brand_gold_or_the_old_green_or_orange(self):
        found = []
        for path in self.files():
            text = (ROOT / path).read_text(encoding="utf-8")
            for match in self.FAINT.finditer(text):
                found.append(f"{path}:{text[:match.start()].count(chr(10)) + 1} {match.group(0)}")
        self.assertEqual(found, [])

    def test_no_white_words_on_gold(self):
        gold = re.compile(r"background(-color)?\s*:\s*var\(--(accent-gold|gold)\)", re.I)
        white = re.compile(r"(?<![-\w])color\s*:\s*(#fff\b|#ffffff\b|white\b)", re.I)
        found = []
        for path in self.files():
            text = (ROOT / path).read_text(encoding="utf-8")
            for rule in re.finditer(r"\{([^{}]*)\}|style=\"([^\"]*)\"", text):
                body = rule.group(1) or rule.group(2) or ""
                if gold.search(body) and white.search(body):
                    found.append(f"{path}:{text[:rule.start()].count(chr(10)) + 1}")
        self.assertEqual(found, [])
