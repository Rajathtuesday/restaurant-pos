"""
Behind Cloudflare, Django must know when the visitor is on https.

Before: Cloudflare reaches nginx over plain http, nginx sets X-Forwarded-Proto
to its own scheme ("http"), so request.is_secure() was always False. Every
link built with build_absolute_uri came out as http://: the Razorpay and
aggregator webhook URLs shown in Setup, WhatsApp bill links. Since Cloudflare
"Always Use HTTPS" was switched on (12 Oct 2026), http:// answers with a 301,
which webhook senders do not follow. HSTS was configured but never sent.

Now core.middleware.CloudflareSchemeMiddleware reads Cloudflare's CF-Visitor,
believed only when the connection to nginx came from a Cloudflare address.

These requests look the way gunicorn sees them in production: REMOTE_ADDR is
nginx (127.0.0.1), X-Real-IP is whoever connected to nginx, and nginx's own
X-Forwarded-Proto says "http".

Run: python manage.py test core.tests.test_https_scheme
"""
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core.middleware import CloudflareSchemeMiddleware
from core.utils import visitor_used_https

CLOUDFLARE_EDGE = "162.158.10.20"        # inside 162.158.0.0/15
ATTACKER = "203.0.113.7"                  # connects to the origin directly
HTTPS_VISITOR = '{"scheme":"https"}'
HTTP_VISITOR = '{"scheme":"http"}'


def _request(**headers):
    base = {
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_HOST": "rasova.net",
        "HTTP_X_FORWARDED_PROTO": "http",   # what nginx always sends
    }
    base.update(headers)
    return RequestFactory().get("/", **base)


def _absolute_link(request):
    """Run the middleware, then build a link the way the Setup views do."""
    response = CloudflareSchemeMiddleware(
        lambda req: HttpResponse(req.build_absolute_uri("/orders/api/razorpay/webhook/"))
    )(request)
    return response.content.decode()


class WhoIsBelievedTest(SimpleTestCase):
    def test_visitor_on_https_through_cloudflare(self):
        request = _request(HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_VISITOR=HTTPS_VISITOR)
        self.assertTrue(visitor_used_https(request))

    def test_header_from_someone_who_went_around_cloudflare_is_ignored(self):
        request = _request(HTTP_X_REAL_IP=ATTACKER, HTTP_CF_VISITOR=HTTPS_VISITOR)
        self.assertFalse(visitor_used_https(request))

    def test_cloudflare_connecting_directly_is_believed_too(self):
        # Same rule as get_client_ip: the connecting machine is Cloudflare's.
        request = _request(REMOTE_ADDR=CLOUDFLARE_EDGE, HTTP_CF_VISITOR=HTTPS_VISITOR)
        self.assertTrue(visitor_used_https(request))

    def test_someone_else_connecting_directly_is_ignored(self):
        request = _request(REMOTE_ADDR=ATTACKER, HTTP_CF_VISITOR=HTTPS_VISITOR)
        self.assertFalse(visitor_used_https(request))

    def test_visitor_on_http(self):
        request = _request(HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_VISITOR=HTTP_VISITOR)
        self.assertFalse(visitor_used_https(request))

    def test_broken_or_missing_header_is_not_https_and_does_not_crash(self):
        for value in ("", "not json", "[1, 2]", '{"scheme": null}'):
            with self.subTest(value=value):
                request = _request(HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_VISITOR=value)
                self.assertFalse(visitor_used_https(request))


@override_settings(ALLOWED_HOSTS=["rasova.net", "localhost"])
class LinksDjangoBuildsTest(SimpleTestCase):
    def test_webhook_link_is_https_for_a_cloudflare_https_visitor(self):
        request = _request(HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_VISITOR=HTTPS_VISITOR)
        self.assertEqual(_absolute_link(request), "https://rasova.net/orders/api/razorpay/webhook/")

    def test_forged_header_does_not_change_the_link(self):
        request = _request(HTTP_X_REAL_IP=ATTACKER, HTTP_CF_VISITOR=HTTPS_VISITOR)
        self.assertEqual(_absolute_link(request), "http://rasova.net/orders/api/razorpay/webhook/")

    def test_local_development_is_unchanged(self):
        request = RequestFactory().get("/", HTTP_HOST="localhost:8000")
        self.assertEqual(_absolute_link(request), "http://localhost:8000/orders/api/razorpay/webhook/")


@override_settings(ALLOWED_HOSTS=["rasova.net"], SECURE_HSTS_SECONDS=86400, SECURE_HSTS_INCLUDE_SUBDOMAINS=True)
class ThroughTheWholeStackTest(TestCase):
    """The middleware runs before SecurityMiddleware, so HSTS now goes out."""

    def _get(self, **headers):
        return self.client.get("/robots.txt", HTTP_HOST="rasova.net", REMOTE_ADDR="127.0.0.1",
                               HTTP_X_FORWARDED_PROTO="http", **headers)

    def test_https_visitor_gets_hsts(self):
        response = self._get(HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_VISITOR=HTTPS_VISITOR)
        self.assertEqual(response.status_code, 200)
        self.assertIn("max-age=86400", response.get("Strict-Transport-Security", ""))

    def test_forged_header_gets_no_hsts(self):
        response = self._get(HTTP_X_REAL_IP=ATTACKER, HTTP_CF_VISITOR=HTTPS_VISITOR)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Strict-Transport-Security", response)
