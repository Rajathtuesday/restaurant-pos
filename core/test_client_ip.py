"""
core.utils.get_client_ip: which forwarded header is believed, and from whom.

Before: CF-Connecting-IP was believed whenever present, then the first hop
of X-Forwarded-For. Anyone reaching the server directly (around Cloudflare)
could send either header and be any IP they liked: a new IP per request to
escape rate limits and login lockouts, or 127.0.0.1 to pass the aggregator
webhook's IP allowlist.

Now X-Real-IP is believed only from nginx (a trusted proxy), and
CF-Connecting-IP only when the connecting address is Cloudflare's.

These requests look the way gunicorn sees them in production: REMOTE_ADDR
is nginx (127.0.0.1) and X-Real-IP is whoever connected to nginx.

Run: python manage.py test core.test_client_ip
"""
import json

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.utils import get_client_ip, ip_in

CLOUDFLARE_EDGE = "162.158.10.20"      # inside 162.158.0.0/15
CLOUDFLARE_EDGE_V6 = "2606:4700::6810:1"
ATTACKER = "203.0.113.7"              # connects to the origin directly
VISITOR = "49.207.1.2"


def _request(remote_addr="127.0.0.1", **headers):
    return RequestFactory().get("/", REMOTE_ADDR=remote_addr, **headers)


class WhichHeaderIsBelievedTest(SimpleTestCase):
    def test_visitor_through_cloudflare(self):
        req = _request(HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_CONNECTING_IP=VISITOR)
        self.assertEqual(get_client_ip(req), VISITOR)

    def test_visitor_through_cloudflare_over_ipv6(self):
        req = _request(HTTP_X_REAL_IP=CLOUDFLARE_EDGE_V6, HTTP_CF_CONNECTING_IP=VISITOR)
        self.assertEqual(get_client_ip(req), VISITOR)

    def test_direct_caller_cannot_claim_localhost(self):
        req = _request(HTTP_X_REAL_IP=ATTACKER, HTTP_CF_CONNECTING_IP="127.0.0.1")
        self.assertEqual(get_client_ip(req), ATTACKER)

    def test_x_forwarded_for_is_never_believed(self):
        # nginx appends to the client's own X-Forwarded-For, so its first hop
        # is whatever the client wrote.
        req = _request(HTTP_X_REAL_IP=ATTACKER, HTTP_X_FORWARDED_FOR="9.9.9.9, " + ATTACKER)
        self.assertEqual(get_client_ip(req), ATTACKER)

    def test_x_real_ip_only_from_a_trusted_proxy(self):
        # Something other than nginx connected to gunicorn: its own address
        # is the answer, not the header it sent.
        req = _request(remote_addr="10.0.0.5", HTTP_X_REAL_IP=VISITOR)
        self.assertEqual(get_client_ip(req), "10.0.0.5")

    def test_garbage_cf_header_from_cloudflare_falls_back_to_the_edge(self):
        req = _request(HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_CONNECTING_IP="not-an-ip")
        self.assertEqual(get_client_ip(req), CLOUDFLARE_EDGE)

    def test_local_development_without_proxies(self):
        self.assertEqual(get_client_ip(_request()), "127.0.0.1")

    def test_ip_in_takes_ranges_and_plain_addresses(self):
        self.assertTrue(ip_in("162.158.0.1", ["162.158.0.0/15"]))
        self.assertTrue(ip_in("1.2.3.4", [" 1.2.3.4 "]))
        self.assertFalse(ip_in("1.2.3.5", ["1.2.3.4"]))
        self.assertFalse(ip_in("nonsense", ["0.0.0.0/0"]))


class LoginLockoutsUseTheSameAnswerTest(SimpleTestCase):
    def test_axes_sees_the_real_address(self):
        from axes.helpers import get_client_ip_address
        req = _request(HTTP_X_REAL_IP=ATTACKER, HTTP_CF_CONNECTING_IP="1.1.1.1")
        self.assertEqual(get_client_ip_address(req), ATTACKER)


@override_settings(RATELIMIT_ENABLE=True)
class RateLimitCannotBeDodgedTest(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _post(self, **headers):
        return self.client.post(
            reverse("create-order"), data=json.dumps({}), content_type="application/json", **headers,
        )

    def test_rotating_a_fake_cloudflare_header_still_hits_the_limit(self):
        for n in range(20):
            resp = self._post(HTTP_X_REAL_IP=ATTACKER, HTTP_CF_CONNECTING_IP=f"10.1.0.{n}")
            self.assertNotEqual(resp.status_code, 429)
        resp = self._post(HTTP_X_REAL_IP=ATTACKER, HTTP_CF_CONNECTING_IP="10.1.0.99")
        self.assertEqual(resp.status_code, 429)

    def test_two_visitors_behind_cloudflare_have_their_own_limits(self):
        for _ in range(20):
            self._post(HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_CONNECTING_IP=VISITOR)
        self.assertEqual(
            self._post(HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_CONNECTING_IP=VISITOR).status_code, 429,
        )
        self.assertNotEqual(
            self._post(HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_CONNECTING_IP="49.207.1.3").status_code, 429,
        )


@override_settings(DEBUG=False, AGGREGATOR_IP_ALLOWLIST=["127.0.0.1"])
class AggregatorAllowlistTest(TestCase):
    def test_direct_caller_claiming_localhost_is_turned_away(self):
        resp = self.client.post(
            reverse("api-ingest-order"), data="{}", content_type="application/json",
            HTTP_X_REAL_IP=ATTACKER, HTTP_CF_CONNECTING_IP="127.0.0.1",
        )
        self.assertEqual(resp.status_code, 403)
