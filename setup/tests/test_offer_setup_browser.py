"""
Setup, Offers in a real browser (static/js/setup/offers.js, 14 Nov 2026).

The page's script moved out of the template into modules: the example
bill, Edit, Pause, Remove and Pause all. Remove and Pause all ask in
Rasova's own sheet (the browser's confirm() never shows in the Android
app). Selectors are ones the old page and the new one share.
Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test setup.tests.test_offer_setup_browser
"""
import re

from core.tests.browser import BrowserTestCase
from menu.models import MenuItem
from offers.models import Offer
from setup.tests import test_offer_setup as base

EXAMPLE = 'return t.text("#example");'


def plain(text):
    """Without currency and minus signs, one space apart."""
    return re.sub(r"\s+", " ", re.sub(r"[^\x00-\x7f]", "", text))


class OfferPageBrowserTest(BrowserTestCase):
    setUp = base.OfferSetupBase.setUp
    user = base.OfferSetupBase.user
    post = base.OfferSetupBase.post
    create = base.OfferSetupBase.create

    def _run(self, steps):
        out = self.run_page("/setup/offers/", steps, user=self.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        return out["results"]

    def test_the_example_bill_prices_buy_two_get_one(self):
        # Whole menu, buy 2 get 1: two Craft (2,500) and one Lager (2,300); the
        # Lager is free. Beer VAT is 0% here.
        example = plain(self._run([("example", EXAMPLE)])["example"])
        self.assertIn("Buy 2 pitchers, get 1 free 2300.00", example)
        self.assertIn("Offers2300.00", example.replace(" ", ""))
        self.assertIn("The guest saves 2300.00.", example)

    def test_the_example_follows_the_kind_of_offer(self):
        example = plain(self._run([
            ("pick", 't.click(\'.tpl[data-tpl="percent_off"]\');'
                     'return await t.until(() => t.text("#example").includes("Offers"));'),
            ("example", EXAMPLE),
        ])["example"]).replace(" ", "")
        # 25% off one of each: 4,800 less 1,200.
        self.assertIn("Offers1200.00", example)
        self.assertIn("Guestpays3600", example)

    def test_a_dish_name_shows_as_text(self):
        MenuItem.objects.filter(pk=self.craft.pk).update(name='<b id="injected">Craft</b>')
        results = self._run([
            ("injected", 'return document.querySelector("#example #injected") !== null;'),
            ("text", 'return t.text("#example").includes("<b id=");'),
        ])
        self.assertFalse(results["injected"])
        self.assertTrue(results["text"])

    def test_edit_loads_the_offer_into_the_form_and_saves_it(self):
        resp = self.create(template="percent_off", name="Lager deal", percent="50", dish_ids=[self.lager.id])
        offer = Offer.objects.get(pk=resp.json()["id"])
        results = self._run([
            ("edit", 't.click(".tbl-btn-edit"); return document.getElementById("form-title").textContent;'),
            ("fields", 'return [document.getElementById("f-name").value, document.getElementById("f-pct").value];'),
            ("save", 'document.getElementById("f-pct").value = "5"; t.click("#create-btn");'
                     'return await t.until(() => t.text("#form-msg").includes("Saved"));'),
        ])
        self.assertEqual(results["edit"], "Edit offer")
        self.assertEqual(results["fields"], ["Lager deal", "50.00"])
        offer.refresh_from_db()
        self.assertEqual(str(offer.percent), "5.00")

    def test_remove_asks_in_rasovas_sheet(self):
        resp = self.create(template="percent_off", name="Lager deal", percent="50", dish_ids=[self.lager.id])
        offer = Offer.objects.get(pk=resp.json()["id"])
        self._run([
            ("sheet", 't.click(".tbl-btn-del"); return !!(await t.until(() => document.querySelector(".swal2-confirm")));'),
            ("removed", 't.click(".swal2-confirm"); return true;', {"selector": ".empty-state"}),
        ])
        offer.refresh_from_db()
        self.assertIsNotNone(offer.archived_at)

    def test_pause_all_asks_in_rasovas_sheet(self):
        self.create()
        self._run([
            ("sheet", 't.click("#pause-all"); return !!(await t.until(() => document.querySelector(".swal2-confirm")));'),
            ("paused", 't.click(".swal2-confirm"); return true;', {"selector": ".badge-paused"}),
        ])
        self.assertFalse(Offer.objects.filter(is_active=True).exists())
