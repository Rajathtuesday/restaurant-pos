"""
Signed out while a screen was open: a page's script is told so (401 and
words) instead of being sent to the sign-in page, the page carries the
notice static/js/core/signed_out.js shows, and the log line says what a
refusal said.

Why (28 Nov 2026, production log): one sign-in covers every tab and every
restaurant address in a browser, so signing out in one tab signed out a
Demo Bistro dashboard in another. Its scripts followed the redirect, got
the sign-in page, said nothing, and went on asking once a minute
("GET /api/notifications/ 302 user=anon") for as long as the tab was open.
The same day a refusal ("POST /pay/89564/ 400") had to be worked out from
the answer's size in bytes, because the log line had no words.

Run: python manage.py test core.tests.test_signed_out
"""
import re
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase

from accounts.models import User
from core.middleware import SIGNED_OUT, RequestLoggingMiddleware, SignedOutScriptMiddleware, refusal_words
from tenants.models import Outlet, Tenant, TenantFeatureOverride

SCRIPT = {"HTTP_SEC_FETCH_DEST": "empty", "HTTP_SEC_FETCH_MODE": "cors"}
PAGE = {"HTTP_SEC_FETCH_DEST": "document", "HTTP_SEC_FETCH_MODE": "navigate",
        "HTTP_ACCEPT": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


class _Kitchen(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Signed Out Pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        TenantFeatureOverride.objects.update_or_create(tenant=self.tenant, feature="kitchen_display",
                                                       defaults={"enabled": True})
        self.chef = User.objects.create_user(username="so_chef", password="pw", tenant=self.tenant,
                                             outlet=self.outlet, role="chef")


class AScriptIsToldItIsSignedOutTest(_Kitchen):
    def assertSignedOut(self, response):
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": SIGNED_OUT, "signed_out": True})

    def test_a_fetch_from_a_page_whose_session_ended(self):
        # Every fetch() a browser sends says Sec-Fetch-Dest: empty.
        self.assertSignedOut(Client().get("/kitchen-data/", **SCRIPT))

    def test_the_shared_clients_marker_and_a_json_accept_count_too(self):
        self.assertSignedOut(Client().get("/api/notifications/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"))
        self.assertSignedOut(Client().get("/dashboard/metrics.json", HTTP_ACCEPT="application/json"))

    def test_a_post_from_a_signed_out_page(self):
        self.assertSignedOut(Client().post("/item-ready/1/", **SCRIPT))

    def test_signing_out_in_another_tab_reaches_this_one(self):
        browser = Client()
        browser.force_login(self.chef)
        self.assertEqual(browser.get("/kitchen-data/", **SCRIPT).status_code, 200)
        browser.get("/logout/", **PAGE)                    # the other tab
        self.assertSignedOut(browser.get("/kitchen-data/", **SCRIPT))

    def test_the_browser_opening_a_page_still_gets_the_sign_in_page(self):
        for headers in (PAGE, {}):
            with self.subTest(headers=bool(headers)):
                response = Client().get("/kitchen/", **headers)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(settings.LOGIN_URL), response["Location"])

    def test_the_401_is_logged_as_the_ordinary_answer_it_replaced_with_its_words(self):
        with self.assertLogs("pos.core", level="INFO") as logs:
            Client().get("/kitchen-data/", **SCRIPT)
        line = next(r for r in logs.records if "/kitchen-data/" in r.getMessage())
        self.assertEqual(line.levelname, "INFO")
        self.assertIn(" 401 ", line.getMessage())
        self.assertIn(f'reason="{SIGNED_OUT}"', line.getMessage())


class OnlyASignedOutScriptSentToSignInTest(SimpleTestCase):
    """The middleware on its own, with the answers a view can give."""

    def answer(self, response, user_signed_in=False, **headers):
        request = RequestFactory().get("/x/", **headers)
        request.user = type("U", (), {"is_authenticated": user_signed_in})()
        return SignedOutScriptMiddleware(lambda r: response)(request)

    def test_a_signed_in_person_turned_away_to_sign_in_keeps_the_redirect(self):
        # A check that sends a signed-in person to the sign-in page is not
        # "signed out": the words would be wrong.
        kept = HttpResponseRedirect(settings.LOGIN_URL + "?next=/x/")
        self.assertIs(self.answer(kept, user_signed_in=True, **SCRIPT), kept)

    def test_a_redirect_somewhere_else_is_left_alone(self):
        kept = HttpResponseRedirect("/dashboard/")
        self.assertIs(self.answer(kept, **SCRIPT), kept)

    def test_an_answer_that_is_not_a_redirect_is_left_alone(self):
        kept = JsonResponse({"ok": True})
        self.assertIs(self.answer(kept, **SCRIPT), kept)

    def test_the_sign_in_address_with_a_host_and_next_still_counts(self):
        response = self.answer(HttpResponseRedirect("https://spice.rasova.net" + settings.LOGIN_URL + "?next=/kitchen-data/"), **SCRIPT)
        self.assertEqual(response.status_code, 401)


class ThePageCarriesTheNoticeTest(_Kitchen):
    def test_a_signed_in_page_has_the_hidden_notice_with_a_way_back(self):
        browser = Client()
        browser.force_login(self.chef)
        html = browser.get("/kitchen/?station=Bar").content.decode()
        notice = re.search(r'<div class="signed-out-note"[^>]*>.*?</div>', html, re.S)
        self.assertIsNotNone(notice, "base.html renders the notice")
        self.assertIn("data-signed-out", notice.group(0))
        self.assertIn(" hidden", notice.group(0).split(">")[0])
        self.assertIn('href="/login/?next=/kitchen/%3Fstation%3DBar"', notice.group(0))
        self.assertIn("stopped updating", notice.group(0))

    def test_the_sign_in_page_itself_has_none(self):
        self.assertNotIn("data-signed-out", Client().get(settings.LOGIN_URL).content.decode())

    def test_the_script_and_the_server_agree_on_the_sign_in_address(self):
        source = (Path(settings.BASE_DIR) / "static" / "js" / "core" / "signed_out.js").read_text(encoding="utf-8")
        self.assertIn(f'export const SIGN_IN = "{settings.LOGIN_URL}";', source)


class TheLogLineSaysWhyTest(SimpleTestCase):
    def test_a_refusal_carries_its_words(self):
        self.assertEqual(refusal_words(JsonResponse({"error": "No open cash session."}, status=400)), "No open cash session.")
        self.assertEqual(refusal_words(JsonResponse({"message": "Could not load table data."}, status=500)), None,
                         "a 5xx has its traceback in the error log already")
        self.assertEqual(refusal_words(JsonResponse({"detail": "Not allowed"}, status=403)), "Not allowed")
        self.assertEqual(refusal_words(JsonResponse({"error": "No printer configured"}, status=422)), "No printer configured")

    def test_nothing_for_a_success_a_404_a_page_or_words_that_are_not_there(self):
        self.assertIsNone(refusal_words(JsonResponse({"error": "x"}, status=200)))
        self.assertIsNone(refusal_words(JsonResponse({"error": "Not found"}, status=404)))
        self.assertIsNone(refusal_words(HttpResponse("<h1>Bad Request</h1>", status=400)))
        self.assertIsNone(refusal_words(JsonResponse({"error": ""}, status=400)))
        self.assertIsNone(refusal_words(JsonResponse([1, 2], safe=False, status=400)))
        self.assertIsNone(refusal_words(HttpResponse("{not json", status=400, content_type="application/json")))

    def test_words_that_carry_what_someone_typed_stay_on_one_line(self):
        said = refusal_words(JsonResponse({"error": "Payment method 'x\n[ERROR] forged line' is not enabled."}, status=400))
        self.assertNotIn("\n", said)
        self.assertEqual(said, "Payment method 'x [ERROR] forged line' is not enabled.")

    def test_long_words_are_cut(self):
        said = refusal_words(JsonResponse({"error": "word " * 100}, status=400))
        self.assertEqual(len(said), 200)
        self.assertTrue(said.endswith("…"))

    def test_the_line_itself(self):
        request = RequestFactory().post("/pay/89564/")
        request.user = type("U", (), {"is_authenticated": False})()
        middleware = RequestLoggingMiddleware(lambda r: JsonResponse({"error": "No open cash session."}, status=400))
        with self.assertLogs("pos.core", level="WARNING") as logs:
            middleware(request)
        self.assertRegex(logs.output[0], r'POST /pay/89564/ 400 .* reason="No open cash session\."$')

    def test_any_other_401_is_still_a_warning(self):
        # A webhook's bad signature, say: someone may be knocking.
        request = RequestFactory().post("/payments/razorpay/webhook/")
        request.user = type("U", (), {"is_authenticated": False})()
        middleware = RequestLoggingMiddleware(lambda r: JsonResponse({"error": "Invalid signature"}, status=401))
        with self.assertLogs("pos.core", level="INFO") as logs:
            middleware(request)
        self.assertEqual(logs.records[0].levelname, "WARNING")
        self.assertIn('reason="Invalid signature"', logs.output[0])

    def test_a_line_with_nothing_to_say_is_as_before(self):
        request = RequestFactory().get("/tables/")
        request.user = type("U", (), {"is_authenticated": False})()
        with self.assertLogs("pos.core", level="INFO") as logs:
            RequestLoggingMiddleware(lambda r: HttpResponse("ok"))(request)
        self.assertNotIn("reason=", logs.output[0])
