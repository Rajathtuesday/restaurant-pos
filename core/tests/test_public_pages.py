"""The public marketing pages: every page in public/ is in the shared list,
indexable on rasova.net, moved off subdomains, in the sitemap, linked from the
home page, and its FAQ markup matches what visitors read.
"""
import html
import json
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings

from core.public_pages import PUBLIC_PAGES, PUBLIC_PATHS

OUTLET_PAGES = ["/fine-dining-pos/", "/cafe-pos/", "/qsr-pos/", "/bar-pos/"]


def page_file(path):
    return Path(settings.WHITENOISE_ROOT) / path.strip("/") / "index.html"


def read(path):
    return page_file(path).read_text(encoding="utf-8")


def meta(text, pattern):
    match = re.search(pattern, text, re.S)
    return html.unescape(match.group(1)) if match else None


class PublicPageListTest(TestCase):

    def test_every_page_in_public_is_listed(self):
        # A new page dropped into public/ without being listed would be served
        # with noindex and left out of the sitemap.
        root = Path(settings.WHITENOISE_ROOT)
        on_disk = {"/" + f.parent.relative_to(root).as_posix().strip(".") + "/"
                   for f in root.rglob("index.html")}
        on_disk = {p.replace("//", "/") for p in on_disk}
        self.assertEqual(on_disk, set(PUBLIC_PATHS))

    def test_the_outlet_pages_are_listed(self):
        for path in OUTLET_PAGES:
            self.assertIn(path, PUBLIC_PATHS)


@override_settings(ALLOWED_HOSTS=["testserver", "rasova.net", ".rasova.net"],
                   CANONICAL_HOST="rasova.net")
class PublicPageServingTest(TestCase):

    def test_each_page_is_served_and_indexable_on_the_main_site(self):
        for page in PUBLIC_PAGES:
            response = self.client.get(page.path, HTTP_HOST="rasova.net")
            self.assertEqual(response.status_code, 200, page.path)
            self.assertNotIn("X-Robots-Tag", response, page.path)

    def test_each_page_other_than_home_moves_off_subdomains(self):
        for page in PUBLIC_PAGES:
            if page.path == "/":
                continue
            response = self.client.get(page.path, HTTP_HOST="spice.rasova.net")
            self.assertEqual(response.status_code, 301, page.path)
            self.assertEqual(response["Location"], f"https://rasova.net{page.path}")

    def test_sitemap_lists_every_page_once(self):
        body = self.client.get("/sitemap.xml", HTTP_HOST="rasova.net").content.decode()
        locs = re.findall(r"<loc>(.*?)</loc>", body)
        self.assertEqual(sorted(locs), sorted(f"https://rasova.net{p.path}" for p in PUBLIC_PAGES))


class PublicPageContentTest(TestCase):

    def test_each_page_names_itself_as_canonical(self):
        for page in PUBLIC_PAGES:
            canonical = meta(read(page.path), r'<link rel="canonical" href="([^"]+)"')
            self.assertEqual(canonical, f"https://rasova.net{page.path}", page.path)

    def test_titles_and_descriptions_are_unique_and_fit_search_results(self):
        titles, descriptions = {}, {}
        for path in OUTLET_PAGES:
            text = read(path)
            title = meta(text, r"<title>(.*?)</title>")
            description = meta(text, r'<meta name="description" content="([^"]*)"')
            self.assertLessEqual(len(title), 65, path)
            self.assertLessEqual(len(description), 160, path)
            titles[title] = path
            descriptions[description] = path
        self.assertEqual(len(titles), len(OUTLET_PAGES))
        self.assertEqual(len(descriptions), len(OUTLET_PAGES))

    def test_home_and_every_outlet_page_link_to_every_outlet_page(self):
        for source in ["/", "/compare/"] + OUTLET_PAGES:
            text = read(source)
            for target in OUTLET_PAGES:
                self.assertIn(f'href="{target}"', text, f"{source} -> {target}")

    def test_faq_schema_matches_the_visible_questions(self):
        for path in OUTLET_PAGES:
            text = read(path)
            blocks = [json.loads(b) for b in re.findall(
                r'<script type="application/ld\+json">(.*?)</script>', text, re.S)]
            faq = next(b for b in blocks if b["@type"] == "FAQPage")
            visible = [html.unescape(re.sub(r"<[^>]+>", "", q)).strip() for q in re.findall(
                r'<button class="faq-question"[^>]*>(.*?)<span', text, re.S)]
            self.assertEqual([q["name"] for q in faq["mainEntity"]], visible, path)

    def test_no_em_dashes_in_the_outlet_pages(self):
        for path in OUTLET_PAGES:
            self.assertNotIn("—", read(path), path)
