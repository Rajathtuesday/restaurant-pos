# core/tests.py
"""
Tests for core/decorators.py.

Coverage:
  - tenant_required now bypasses for superusers, who normally have
    tenant=None and would otherwise be locked out of any view stacked
    with this decorator, including admin/support tooling.
"""
import json

from django.http import HttpResponse
from django.test import TestCase, RequestFactory, Client
from django.urls import reverse

from accounts.models import User
from core.decorators import tenant_required
from tenants.models import Tenant, Outlet


@tenant_required
def _dummy_view(request):
    return HttpResponse("ok")


class TenantRequiredSuperuserBypassTest(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(name="Decorator Test Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

    def test_superuser_with_no_tenant_is_not_blocked(self):
        superuser = User.objects.create_superuser(
            username="platform_admin", password="pw", email="a@a.com",
        )
        self.assertIsNone(superuser.tenant)

        request = self.factory.get("/some-tenant-scoped-view/")
        request.user = superuser
        response = _dummy_view(request)
        self.assertEqual(response.status_code, 200)

    def test_regular_user_without_tenant_still_blocked(self):
        # Existing behavior must be unchanged for non-superusers — this
        # decorator's whole job for everyone else is exactly this check.
        from django.core.exceptions import PermissionDenied

        plain_user = User.objects.create_user(
            username="no_tenant_user", password="pw",
        )
        self.assertFalse(plain_user.is_superuser)
        self.assertIsNone(plain_user.tenant)

        request = self.factory.get("/some-tenant-scoped-view/")
        request.user = plain_user
        with self.assertRaises(PermissionDenied):
            _dummy_view(request)

    def test_regular_user_with_tenant_and_outlet_passes_normally(self):
        user = User.objects.create_user(
            username="scoped_user", password="pw",
            tenant=self.tenant, outlet=self.outlet,
        )
        request = self.factory.get("/some-tenant-scoped-view/")
        request.user = user
        response = _dummy_view(request)
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_user_redirected_to_login(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.get("/some-tenant-scoped-view/")
        request.user = AnonymousUser()
        response = _dummy_view(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class CsrfFailureViewTest(TestCase):
    """
    Django's test Client normally bypasses CSRF checks entirely —
    enforce_csrf_checks=True is required to actually trigger a real
    failure and exercise CSRF_FAILURE_VIEW, same as a real browser would.
    """

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def test_json_request_gets_json_error_not_html(self):
        # No csrfmiddlewaretoken, no X-CSRFToken header — a guaranteed
        # CSRF failure, sent the way apiClient actually sends requests.
        response = self.client.post(
            reverse("login"),
            data=json.dumps({"username": "x", "password": "y"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        data = json.loads(response.content)
        self.assertIn("refresh", data["error"].lower())

    def test_form_request_gets_friendly_html_page_not_django_default(self):
        response = self.client.post(
            reverse("login"),
            data={"username": "x", "password": "y"},
        )
        self.assertEqual(response.status_code, 403)
        content = response.content.decode()
        # The custom page, not Django's built-in "Forbidden (403)" default.
        self.assertIn("Reload Page", content)
        self.assertNotIn("CSRF verification failed", content)


class NoMultilineDjangoCommentsTest(TestCase):
    """
    Django's {# #} comment tag cannot span multiple lines -- unlike most
    template languages, that's a real, documented Django limitation, not a
    style nit. A multi-line {# ... #} isn't parsed as a comment at all: it's
    literal text, output verbatim, delimiters included. Hit this for real in
    templates/core/base.html (rendered on every authenticated page) and
    setup/aggregator_config.html (right above a secret-key input field) --
    both leaked internal review comments straight onto the live page. The
    fix is always {% comment %}...{% endcomment %}, which does support
    multiple lines. This scans every template once so the mistake can't
    silently reappear elsewhere.
    """

    def test_no_multiline_hash_comments_in_any_template(self):
        import re
        from pathlib import Path

        offenders = []
        pattern = re.compile(r"\{#(.*?)#\}", re.DOTALL)
        for path in Path(".").rglob("templates/**/*.html"):
            if ".venv" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for m in pattern.finditer(text):
                if "\n" in m.group(1):
                    line_no = text.count("\n", 0, m.start()) + 1
                    offenders.append(f"{path}:{line_no}")

        self.assertEqual(
            offenders, [],
            "Multi-line {# #} comment(s) found -- Django will NOT strip "
            "these, they render as literal text. Use {% comment %}...{% "
            "endcomment %} instead: " + ", ".join(offenders)
        )


class UnscopedCallSiteAllowlistTest(TestCase):
    """
    .unscoped(reason=...) (core/models.py TenantManager) is the only
    sanctioned way to bypass tenant auto-scoping. This scans the whole
    codebase for call sites and asserts they match an exact, hardcoded
    allowlist -- a new, un-reviewed call site fails this test until a
    human deliberately adds it below. This is the direct fix for the
    failure pattern that motivated the whole tenant-isolation effort: a
    second, unreviewed copy of sensitive logic diverging from the first
    because nothing forced it to stay in sync (restaurant creation used
    to exist near-identically in portal/views.py and
    accounts/views/superuser_views.py, and a security fix landed in
    only one of them).

    Currently empty: portal/views.py and accounts/views/superuser_views.py
    are both gated to is_superuser=True (checked directly, not via a
    decorator that could vary), and a superuser account always has
    tenant=None -- so the ambient auto-scope already no-ops for every
    query they run, with no explicit .unscoped() call needed. If a
    future role other than is_superuser ever needs cross-tenant
    visibility, .unscoped() is where that access should be added,
    deliberately, and this allowlist is where it gets reviewed.
    """
    ALLOWED_CALL_SITES = {
        # Data-integrity preflight: must see every tenant's rows to
        # report null/orphaned tenant_id counts across the whole table,
        # not just the (nonexistent, since it's a management command)
        # current request's tenant. See tenants/management/commands/
        # tenant_isolation_preflight.py's own docstring.
        "tenants/management/commands/tenant_isolation_preflight.py:47",
        "tenants/management/commands/tenant_isolation_preflight.py:52",
    }

    # Files that reference ".unscoped(" in prose (docstrings/comments)
    # rather than as an actual call -- the definition itself, and this
    # test's own docstring above.
    _NOT_CALL_SITES = {"core/models.py", "core/tests.py"}

    def test_unscoped_call_sites_match_allowlist(self):
        import re
        from pathlib import Path

        pattern = re.compile(r"\.unscoped\(")
        found = set()
        for path in Path(".").rglob("*.py"):
            if ".venv" in path.parts or "migrations" in path.parts:
                continue
            posix = path.as_posix()
            if posix in self._NOT_CALL_SITES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for m in pattern.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                found.add(f"{posix}:{line_no}")

        unexpected = found - self.ALLOWED_CALL_SITES
        missing = self.ALLOWED_CALL_SITES - found
        self.assertEqual(
            unexpected, set(),
            f"New, un-reviewed .unscoped() call site(s): {sorted(unexpected)}. "
            f"If deliberate, add to ALLOWED_CALL_SITES above after review."
        )
        self.assertEqual(
            missing, set(),
            f"Allowlisted .unscoped() call site(s) no longer found, remove "
            f"from ALLOWED_CALL_SITES: {sorted(missing)}"
        )


class HeaderLeftHasWayBackTest(TestCase):
    """
    base.html's default header_left is a link to /dashboard/. Pages that
    override the block to show a page-specific title need to keep wrapping
    it in *some* link -- back to /dashboard/, or a breadcrumb to a parent
    page -- or that title becomes plain unclickable text and the page has
    no way back to anywhere. Found this for real on Order History and
    every Shifts page (all converted in the same batch, all missing it) --
    reported by a user with no way back except the browser back button.

    A handful of pages are correctly exempt: top-level superuser/portal
    landing pages with no parent to link to, the onboarding wizard
    (deliberately no exit), and bill.html (its title is a plain "Invoice"
    label -- it has its own explicit, context-aware back-navigation lower
    in the page instead).
    """

    EXEMPT = {
        "accounts/templates/accounts/superuser_panel.html",
        "accounts/templates/accounts/feature_flags.html",
        "portal/templates/portal/home.html",
        "setup/templates/setup/onboard.html",
        "orders/templates/orders/bill.html",
    }

    def test_every_header_left_override_has_a_link_somewhere(self):
        import re
        from pathlib import Path

        offenders = []
        pattern = re.compile(r"\{% block header_left %\}(.*?)\{% endblock", re.DOTALL)
        for path in Path(".").rglob("templates/**/*.html"):
            if ".venv" in path.parts:
                continue
            posix = path.as_posix()
            if posix in self.EXEMPT:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            m = pattern.search(text)
            if not m:
                continue
            if "href=" not in m.group(1):
                offenders.append(posix)

        self.assertEqual(
            offenders, [],
            "header_left override with no link at all -- the page title is "
            "plain unclickable text and there's no way back to anywhere: "
            + ", ".join(offenders)
        )
