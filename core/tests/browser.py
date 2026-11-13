"""
A page in a real browser, for the tests that need one (Playwright,
Chromium, a live test server), as orders/tests/test_approval_pin_browser.py
and the other browser tests do.

Rendering a template and reading its HTML proves the markup; running a
module in node with fakes proves its logic. Neither proves that the page,
its modules, Bootstrap and the server work together when a cashier taps a
button. BrowserTestCase.run_page loads a page as a user, runs steps in it
and returns what they returned along with every error the browser saw.

Skipped when Playwright or its Chromium isn't installed (CI has neither).
"""
import os
from unittest import SkipTest
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings

# What a step can use, as `t`, on every page the browser opens:
#   t.until(fn)        wait until fn() is truthy (15 seconds at most), return it.
#                      It returns the moment fn() holds, so a passing test is no
#                      slower; the ceiling is for a loaded machine (a full parallel
#                      run once took three times as long, and a three-request step
#                      needed more than 5 seconds).
#   t.click(selector)  click the first match (it must exist)
#   t.text(selector)   its text, trimmed
#   t.all(selector)    every match, as an array
HELPERS = """
window.t = {
  async until(fn, ms = 15000) {
    const end = Date.now() + ms;
    for (;;) {
      const value = await fn();
      if (value) return value;
      if (Date.now() > end) throw new Error("waited " + ms + " ms for: " + fn);
      await new Promise((r) => setTimeout(r, 25));
    }
  },
  click(selector) {
    const el = document.querySelector(selector);
    if (!el) throw new Error("nothing matches " + selector);
    el.click();
  },
  text(selector) {
    const el = document.querySelector(selector);
    if (!el) throw new Error("nothing matches " + selector);
    return el.textContent.trim();
  },
  all: (selector) => Array.from(document.querySelectorAll(selector)),
};
"""


def _wait_until_the_server_is_idle(timeout=15):
    """Return once no request of the live server is still working in this
    test's database.

    Closing the browser does not stop a request it already sent: a page
    that polls (the token counter asks for its order every 5 seconds) can
    leave one running in the server's thread. The test's cleanup then
    empties every table while that request reads them, and Postgres
    deadlocks the two (12 November 2026, the token counter test in a full
    parallel run). Waiting here puts the cleanup after the last request.

    pg_stat_activity is frozen for the length of a transaction, at the
    first look, so the snapshot is dropped before every look."""
    import time

    from django.db import connection
    if connection.vendor != "postgresql":
        return
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND state <> 'idle'"
            )
            if cursor.fetchone()[0] == 0:
                return
            time.sleep(0.05)


def _console_error(message):
    """A console error, with the address that failed when it is a request
    ("console.error: ... 400 (Bad Request) /pay/7/"), so a test can expect
    a refusal by where it came from."""
    url = (message.location or {}).get("url") or ""
    where = urlparse(url).path if "/static/" not in url else ""
    return f"console.error: {message.text}" + (f" {where}" if where else "")


@override_settings(SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
                   SESSION_COOKIE_DOMAIN=None, CSRF_COOKIE_DOMAIN=None)
class BrowserTestCase(StaticLiveServerTestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SkipTest("Playwright is not installed")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as e:
            cls.playwright.stop()
            super().tearDownClass()
            raise SkipTest(f"Chromium is not available: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def run_page(self, url, steps, user=None, viewport=None):
        """Load `url` (on the live server) as `user` and run `steps` in the
        page. Returns {"errors": [...], "results": {name: value}}.

        A step is (name, javascript): the body of an async function, whose
        return value is the step's result. A step that sends the browser
        somewhere, or reloads the page, says what to wait for as a third
        item, so the next step does not run on a page that is going away:
          (name, javascript, "/bill/7/")       the address starts with this
          (name, javascript, {"selector": s})  an element matching s is there

        An error is an uncaught exception in the page, a console.error
        (which is how Chromium reports a failed request), or a step that
        threw; the run stops at the first step that throws."""
        size = {"width": 1400, "height": 900, **(viewport or {})}
        mobile = bool(size.pop("mobile", False))
        context = self.browser.new_context(viewport=size, is_mobile=mobile, has_touch=mobile)
        try:
            if user is not None:
                client = Client()
                client.force_login(user)
                context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                      "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                                      "url": self.live_server_url}])
            context.add_init_script(HELPERS)
            page = context.new_page()
            page.set_default_timeout(30_000)
            errors, results = [], {}
            page.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
            page.on("console", lambda message: errors.append(_console_error(message))
                    if message.type == "error" else None)
            page.goto(self.live_server_url + url, wait_until="load")
            for step in steps:
                name, script = step[0], step[1]
                try:
                    results[name] = page.evaluate("async () => {" + script + "}")
                    if len(step) > 2 and isinstance(step[2], dict):
                        page.wait_for_selector(step[2]["selector"])
                    elif len(step) > 2:
                        page.wait_for_url(lambda address, path=step[2]: urlparse(address).path.startswith(path),
                                          wait_until="load")
                except Exception as e:  # noqa: BLE001 -- reported to the test as an error
                    errors.append(f'step "{name}": {e}')
                    break
            return {"errors": errors, "results": results}
        finally:
            context.close()
            _wait_until_the_server_is_idle()
