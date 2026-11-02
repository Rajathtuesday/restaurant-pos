"""
No two URLs may share a name.

Found 2 Nov 2026: accounts/urls.py named the owner's dashboard "dashboard",
and reports/urls.py named the reports dashboard "dashboard" too. Neither app
sets app_name, so every name in Rasova lives in one flat list, and when two
patterns share a name, reverse() and {% url %} quietly return the one Django
meets last: /reports/dashboard/. Nothing errors when that happens. The
owner-dashboard permission tests were opening the reports page (and passing,
because both pages turn chefs and waiters away); the feature flags page's
"Dashboard" button went there; and a new sales agent, redirected to
"dashboard", got a 403 and couldn't use Rasova.

This test walks the whole URL map, into every include(), and fails if a name
is used twice in the same namespace, so the next clash fails here instead of
in front of a restaurant. Django allows one name on several patterns that
take different arguments; Rasova doesn't use that, and one page per name
keeps reverse() predictable, so this test doesn't allow it either.

Run: python manage.py test core.tests.test_url_names
"""
from collections import defaultdict

from django.http import HttpResponse
from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver, include, path


def named_routes(patterns, namespace="", prefix=""):
    """(namespace, name, full route) for every named pattern, walking into include()s."""
    for p in patterns:
        if isinstance(p, URLResolver):
            ns = namespace
            if p.namespace:
                ns = f"{namespace}:{p.namespace}" if namespace else p.namespace
            yield from named_routes(p.url_patterns, ns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern) and p.name:
            yield namespace, p.name, prefix + str(p.pattern)


def shared_names(patterns):
    """{name: [routes]} for every name (with its namespace) that more than one pattern uses."""
    routes = defaultdict(list)
    for namespace, name, route in named_routes(patterns):
        routes[f"{namespace}:{name}" if namespace else name].append(route)
    return {name: found for name, found in routes.items() if len(found) > 1}


def _view(request):
    return HttpResponse()


class UrlNamesTest(SimpleTestCase):

    def test_no_two_urls_share_a_name(self):
        shared = shared_names(get_resolver().url_patterns)
        self.assertEqual(
            shared, {},
            "These URL names are used by more than one pattern, so reverse() and "
            "{% url %} quietly pick one of them. Give each page its own name: "
            + "; ".join(f"{name}: {', '.join(found)}" for name, found in shared.items()),
        )

    def test_the_check_catches_a_shared_name(self):
        # The 2 Nov 2026 clash, rebuilt: two apps both included at the root.
        accounts = ([path("dashboard/", _view, name="dashboard")], None)
        reports = ([path("dashboard/", _view, name="dashboard")], None)
        patterns = [path("", include(accounts)), path("reports/", include(reports))]

        self.assertEqual(shared_names(patterns), {"dashboard": ["dashboard/", "reports/dashboard/"]})

    def test_the_same_name_in_two_namespaces_is_fine(self):
        menu = ([path("", _view, name="list")], "menu")
        orders = ([path("", _view, name="list")], "orders")
        patterns = [path("menu/", include(menu)), path("orders/", include(orders))]

        self.assertEqual(shared_names(patterns), {})
