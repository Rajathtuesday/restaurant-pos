"""
Error pages for a request that fails before it is signed in.

Why: from 21 Nov 2026 production answered requests for an unknown host
(bots trying the server's bare IP) with a 500 instead of a 400. The host is
refused in core.middleware.SearchIndexingMiddleware, before
AuthenticationMiddleware, so the request has no .user yet. Django's 400
page renders 400.html with every context processor, and
core.context_processors.tenant_features read request.user: AttributeError,
so the 500 page, which crashed the same way. frontend() also asked the
refused host for itself (request.get_host() raises DisallowedHost again).

Every context processor must answer for a request with no user, no session
and a host Rasova refuses: that is what any error page raised before sign-in
renders with.

Run: python manage.py test core.tests.test_error_pages_early
"""
from django.conf import settings
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils.module_loading import import_string

ONLY_RASOVA = ["rasova.net", ".rasova.net"]


@override_settings(ALLOWED_HOSTS=ONLY_RASOVA)
class ContextProcessorsBeforeSignInTest(SimpleTestCase):

    def test_every_context_processor_answers_a_request_with_no_user_and_a_refused_host(self):
        request = RequestFactory().get("/", HTTP_HOST="1.2.3.4")
        self.assertFalse(hasattr(request, "user"))
        processors = [p for t in settings.TEMPLATES for p in t.get("OPTIONS", {}).get("context_processors", [])]
        self.assertIn("core.context_processors.tenant_features", processors)
        for path in processors:
            with self.subTest(processor=path):
                self.assertIsInstance(import_string(path)(request), dict)

    def test_no_user_means_no_features_and_no_page_data_for_one(self):
        from core.context_processors import frontend, tenant_features
        request = RequestFactory().get("/", HTTP_HOST="1.2.3.4")
        self.assertEqual(tenant_features(request), {"tenant_features": [], "tenant_type": "fine_dining"})
        page = frontend(request)["rasova_page"]
        self.assertFalse(page["authenticated"])
        self.assertNotIn("agent_key", page)
        # the refused host is never echoed back as the page's own address
        self.assertNotIn("1.2.3.4", page["server_url"])


@override_settings(DEBUG=False, ALLOWED_HOSTS=ONLY_RASOVA)
class UnknownHostTest(TestCase):

    def test_an_unknown_host_gets_a_400_not_a_500(self):
        response = Client(raise_request_exception=False).get("/", HTTP_HOST="1.2.3.4")
        self.assertEqual(response.status_code, 400)

    def test_rasova_itself_still_answers(self):
        response = Client(raise_request_exception=False).get("/", HTTP_HOST="rasova.net")
        self.assertEqual(response.status_code, 200)
