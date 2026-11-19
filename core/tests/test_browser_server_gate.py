"""
A browser test's cleanup never runs under a request the page sent
(19 Nov 2026).

Why: a browser test cleans up by emptying every table (flush). A request
the page had sent could still be running in the live server's thread, and
Postgres deadlocked the two ("deadlock detected" in flush, then "duplicate
key ... tenant name" in the next test of the class), once in a few full
parallel runs. The old guard asked Postgres whether any connection was
busy; a request still in Python looks idle there. The server now counts
its requests at the door (core.tests.browser._ServerGate), and the
cleanup waits for the count to reach nothing with the door shut.
Run: python manage.py test core.tests.test_browser_server_gate
"""
import threading
import time

from django.test import SimpleTestCase, override_settings

from core.tests import browser_gate_urls
from core.tests.browser import BrowserTestCase


@override_settings(ROOT_URLCONF="core.tests.browser_gate_urls")
class TheServerFinishesBeforeTheTestMovesOnTest(BrowserTestCase):

    def setUp(self):
        browser_gate_urls.FINISHED.clear()

    def test_a_request_still_running_when_the_page_closes_is_waited_for(self):
        out = self.run_page("/page/", [
            # Sent and not awaited: the page closes while the server works.
            ("fire", 'fetch("/slow/"); await new Promise((r) => setTimeout(r, 300)); return true;'),
        ])
        self.assertEqual(out["errors"], [])
        self.assertTrue(browser_gate_urls.FINISHED.is_set(),
                        "run_page returned while the server was still answering /slow/")


class EveryBrowserTestUsesTheGateTest(SimpleTestCase):
    """The gate guards only tests built on BrowserTestCase. Three test files
    copied its setup onto StaticLiveServerTestCase instead (moved on 19 Nov);
    a live server test written that way again would clean up unguarded."""

    def test_no_test_starts_a_live_server_of_its_own(self):
        import os
        from pathlib import Path

        from django.conf import settings
        root = Path(settings.BASE_DIR)
        skip = {".venv", "venv", "node_modules", "staticfiles", "graft", "md_files", ".git", "__pycache__"}
        allowed = {"core/tests/browser.py", "core/tests/test_browser_server_gate.py"}
        own = []
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for name in files:
                if not name.endswith(".py"):
                    continue
                rel = (Path(folder) / name).relative_to(root)
                if rel.as_posix() in allowed:
                    continue
                if "LiveServerTestCase" in (root / rel).read_text(encoding="utf-8", errors="ignore"):
                    own.append(str(rel))
        self.assertEqual(own, [], "build browser tests on core.tests.browser.BrowserTestCase")


def _app(release, calls):
    def application(environ, start_response):
        calls.append(environ["PATH_INFO"])
        release.wait(5)
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]
    return application


def _serve(app, path):
    """What the server does with one request: call the app, send the body,
    close it."""
    body = app({"PATH_INFO": path}, lambda status, headers: None)
    sent = b"".join(body)
    body.close()
    return sent


class ServerGateTest(SimpleTestCase):

    def setUp(self):
        from core.tests.browser import _gated, _ServerGate
        self.gate = _ServerGate()
        self.release = threading.Event()
        self.calls = []
        self.app = _gated(self.gate, _app(self.release, self.calls))

    def test_drain_waits_for_a_request_in_flight(self):
        worker = threading.Thread(target=_serve, args=(self.app, "/slow/"))
        worker.start()
        while not self.calls:
            time.sleep(0.01)
        threading.Timer(0.3, self.release.set).start()
        started = time.monotonic()
        self.gate.drain(timeout=5)
        self.assertGreaterEqual(time.monotonic() - started, 0.25)
        worker.join(5)

    def test_a_closed_gate_answers_503_without_reaching_the_app(self):
        self.gate.close()
        statuses = []
        body = self.app({"PATH_INFO": "/late/"}, lambda status, headers: statuses.append(status))
        self.assertEqual(statuses, ["503 Service Unavailable"])
        self.assertEqual(self.calls, [])
        self.assertIn(b"cleaning up", b"".join(body))
        self.gate.drain(timeout=0.1)       # nothing was let in

    def test_an_answer_that_never_ends_fails_by_its_address(self):
        worker = threading.Thread(target=_serve, args=(self.app, "/stuck/"))
        worker.start()
        while not self.calls:
            time.sleep(0.01)
        with self.assertRaisesRegex(AssertionError, "/stuck/"):
            self.gate.drain(timeout=0.2)
        self.release.set()
        worker.join(5)
        self.gate.drain(timeout=1)

    def test_a_body_sent_but_never_closed_still_leaves(self):
        # Django's server reads the rest of the request before it closes the
        # body; when the browser has gone that read can fail, and close()
        # never comes.
        self.release.set()
        body = self.app({"PATH_INFO": "/gone/"}, lambda status, headers: None)
        self.assertEqual(b"".join(body), b"ok")
        self.gate.drain(timeout=0.2)

    def test_an_app_that_raises_leaves(self):
        from core.tests.browser import _gated

        def broken(environ, start_response):
            raise RuntimeError("boom")
        app = _gated(self.gate, broken)
        with self.assertRaises(RuntimeError):
            app({"PATH_INFO": "/boom/"}, lambda status, headers: None)
        self.gate.drain(timeout=0.2)
