"""
Every tag in every template is closed before the next one opens.

Why: on 12 November 2026 phase 0 of the frontend plan swapped the Google
Fonts links for Rasova's own vendor/fonts/fonts.css, and in seven
templates the new <link> lost its closing ">". A browser then reads the
next tag as more attributes of that link and drops it:
  * the QR menu (the one page a guest sees) lost its <style> tag, so its
    whole stylesheet was printed on the page as text, above the menu;
  * login, the running order, the order-locked page, inspect and the demo
    switch silently lost the stylesheet on the next line (Bootstrap or its
    icons).
No test noticed, because every page still returned 200 and a template
render test reads the HTML as text, not as a browser parses it.

This reads each template the way that mistake shows: Django's own tags,
comments and inline scripts set aside, a "<" may not start a new tag while
the previous tag is still open.

Run: python manage.py test core.tests.test_template_tags_closed
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)
SKIP_PARTS = {"md_files", "node_modules", "staticfiles", "rasova_android", "public", ".venv", "graft", "docs"}

# A tag's name, then anything but "<" or ">", then a "<" that starts another tag.
OPEN_INTO_NEXT = re.compile(r"<([a-zA-Z][\w-]*)\b[^<>]*<(?=[a-zA-Z/!])")
SET_ASIDE = (
    re.compile(r"\{[{%#].*?[%}#]\}", re.S),                                    # Django tags and variables
    re.compile(r"<!--.*?-->", re.S),                                           # comments
    re.compile(r"(<script\b(?![^>]*\bsrc=)[^>]*>).*?(?=</script>)", re.S | re.I),  # inline script bodies
)


def blank(match):
    """Keep the newlines, so a line number still points at the template."""
    text = match.group(0)
    if match.re.groups:                        # an inline script: keep its opening tag
        head = match.group(1)
        return head + re.sub(r"[^\n]", "", text[len(head):])
    return re.sub(r"[^\n]", "", text)


def unclosed_tags(text):
    """[(line, what follows)] for each tag left open into the next one."""
    for pattern in SET_ASIDE:
        text = pattern.sub(blank, text)
    return [(text.count("\n", 0, m.start()) + 1, " ".join(text[m.start():m.end() + 20].split()))
            for m in OPEN_INTO_NEXT.finditer(text)]


class TemplateTagsClosedTest(SimpleTestCase):

    def test_the_check_sees_the_mistake_and_nothing_else(self):
        broken = '<head>\n  <link rel="stylesheet" href="{% static \'a.css\' %}"\n  <style>body{}</style>\n</head>'
        self.assertEqual([line for line, _ in unclosed_tags(broken)], [2])
        fine = ('<link rel="stylesheet" href="{% static \'a.css\' %}">\n'
                '<a href="{% url \'x\' %}" class="{% if on %}on{% endif %}">go</a>\n'
                '<script>if (a <b) { el.innerHTML = "<div>" }</script>\n<!-- <link -->\n'
                '<input value="{{ v }}" {% if r %}required{% endif %}>')
        self.assertEqual(unclosed_tags(fine), [])

    def test_no_template_leaves_a_tag_open(self):
        found = []
        for path in sorted(BASE.rglob("*.html")):
            rel = path.relative_to(BASE)
            if any(part in SKIP_PARTS for part in rel.parts[:-1]):
                continue
            for line, near in unclosed_tags(path.read_text(encoding="utf-8", errors="ignore")):
                found.append(f"{rel.as_posix()}:{line}  {near}")
        self.assertEqual(found, [], "a tag is not closed before the next one opens:\n" + "\n".join(found))
