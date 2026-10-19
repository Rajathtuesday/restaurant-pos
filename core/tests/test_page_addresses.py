"""
Every address a page's JavaScript calls must exist.

Found 19 Oct 2026: the bill page posted dish discounts to
/orders/item-discount/<id>/ and fetched the PC print agent's receipt from
/orders/qz-data/<id>/, but the orders app is mounted at the site root, so
both answered 404 (since 31 Mar 2026) and, because the 404 page isn't
JSON, the button did nothing at all. A third, /call-waiter/, sat in a
template nothing used. Addresses written by hand in JavaScript are never
checked by Django; {% url %} ones are. This test reads every literal
address in a fetch() or apiClient call in the templates and static
scripts, fills in its variable parts, and resolves it against the URL
map, so a wrong one fails here instead of on a restaurant's bill.

Run: python manage.py test core.tests.test_page_addresses
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

ROOT = Path(settings.BASE_DIR)
SKIP_DIRS = {".venv", "md_files", "node_modules", "staticfiles", "graft", ".git", "rasova_android"}

# fetch("/x/"), apiClient.post(`/x/${id}/`), ... with a literal first argument.
CALL = re.compile(
    r"""(?:\bfetch|apiClient\.(?:get|post|put|delete))\s*\(\s*([`'"])(/[^`'"]*)\1(\s*\+)?"""
)
PIECE = re.compile(r"\$\{[^}]*\}|\{\{[^}]*\}\}")
# What a variable piece can stand for: an id, a QR token, a slug.
SAMPLES = ("1", "11111111-1111-1111-1111-111111111111", "abc")


def calls():
    """(file:line, address as written, whether code appends to it)."""
    for path in ROOT.rglob("*"):
        if path.suffix not in (".html", ".js") or SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in CALL.finditer(text):
            where = f"{path.relative_to(ROOT)}:{text[:match.start()].count(chr(10)) + 1}"
            yield where, match.group(2), bool(match.group(3))


def resolves(address, appended):
    """True if some filling-in of the variable parts is a real address."""
    address = address.split("?")[0]
    if "{%" in address:            # built by Django's own {% url %}: checked by Django
        return True
    for sample in SAMPLES:
        url = PIECE.sub(sample, address)
        if appended:               # "/pay/" + id + "/"
            url = url.rstrip("/") + f"/{sample}/"
        try:
            resolve(url)
            return True
        except Resolver404:
            continue
    return False


class PageAddressesTest(SimpleTestCase):

    def test_every_called_address_exists(self):
        found = list(calls())
        self.assertGreater(len(found), 50)          # the scan really reads the pages
        missing = [f"{where}  {address}" for where, address, appended in found
                   if not resolves(address, appended)]
        self.assertEqual(missing, [], "JavaScript calls addresses that don't exist")

    def test_the_check_catches_a_wrong_address(self):
        self.assertFalse(resolves("/orders/item-discount/${itemId}/", False))
        self.assertTrue(resolves("/item-discount/${itemId}/", False))
        self.assertTrue(resolves("/pay/", True))
