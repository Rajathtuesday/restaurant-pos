"""
A page in a real browser, for the tests that need one (Playwright,
Chromium, a live test server), as orders/tests/test_approval_pin_browser.py
and the other browser tests do.

Rendering a template and reading its HTML proves the markup; running a
module in node with fakes proves its logic. Neither proves that the page,
its modules, Bootstrap and the server work together when a cashier taps a
button. BrowserTestCase.run_page loads a page as a user, runs steps in it
and returns what they returned along with every error the browser saw.

Skipped when Playwright or its Chromium isn't installed (CI installs both).
Every browser test is built on BrowserTestCase: its live server counts the
requests it is working on (_ServerGate), and a test's cleanup waits for the
last one before it empties the tables.
"""
import os
from functools import partial
from unittest import SkipTest
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.staticfiles.handlers import StaticFilesHandler
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

# The Rasova print agent listens on port 8765 on the restaurant's own
# machine, and several pages ask it whether it is there (Kitchen Stations on
# load, the counter when it prints). No agent runs beside a test, and how the
# refusal shows depends on the operating system: Linux refuses at once and
# Chromium logs "WebSocket connection ... failed: ERR_CONNECTION_REFUSED" as a
# console error; Windows retries for about 2 s, past the page's own 2 s
# timeout, and nothing is logged. So the same test passed on Windows and
# failed on Linux (21 Nov 2026). In every test page a socket to that port is
# this stand-in: it says "not running" at once, without the network.
NO_LOCAL_AGENT = """
(() => {
  const Real = window.WebSocket;
  if (!Real) return;
  class NoAgent extends EventTarget {
    constructor(url) {
      super();
      this.url = String(url);
      this.readyState = Real.CONNECTING;
      this.onopen = this.onmessage = this.onerror = this.onclose = null;
      setTimeout(() => {
        this.readyState = Real.CLOSED;
        for (const kind of ["error", "close"]) {
          const event = new Event(kind);
          this.dispatchEvent(event);
          if (typeof this["on" + kind] === "function") this["on" + kind](event);
        }
      }, 0);
    }
    send() { throw new DOMException("the print agent is not running", "InvalidStateError"); }
    close() { this.readyState = Real.CLOSED; }
  }
  window.WebSocket = new Proxy(Real, {
    construct(target, args) {
      return /:8765(\\/|$)/.test(String(args[0])) ? new NoAgent(args[0]) : new target(...args);
    },
  });
})();
"""


class _NoLocalAgentBrowser:
    """The test class's browser: every context it opens gets NO_LOCAL_AGENT,
    whether a test goes through run_page or opens its own context."""

    def __init__(self, browser):
        self._browser = browser

    def new_context(self, **options):
        context = self._browser.new_context(**options)
        context.add_init_script(NO_LOCAL_AGENT)
        return context

    def new_page(self, **options):
        return self.new_context(**options).new_page()

    def __getattr__(self, name):
        return getattr(self._browser, name)


class _ServerGate:
    """Every request the live test server is working on, counted at the
    door, so a test's cleanup can wait for the last one to finish.

    Why (19 Nov 2026): a TransactionTestCase cleans up by emptying every
    table (flush, one TRUNCATE), and a request still running in the
    server's thread could hold one table while the TRUNCATE held another:
    Postgres called it a deadlock, the test errored, and the next test of
    the class met the rows left behind ("duplicate key ... tenant name").
    Closing the browser does not stop a request it already sent. The
    earlier guard asked Postgres whether any connection was busy, but a
    request still in Python (middleware, between two queries) looks idle
    there, so the cleanup started under it once in a few full runs.

    The gate sees the request from its first byte to its last: close()
    turns every later request away with a 503 before it reaches Django,
    and drain() waits until none is left. An answer still running after
    the wait fails the test by its address instead of deadlocking."""

    def __init__(self):
        import threading
        self._cond = threading.Condition()
        self._running = {}      # id -> address, for the error message
        self._next = 0
        self._open = True

    def enter(self, path):
        with self._cond:
            if not self._open:
                return None
            self._next += 1
            self._running[self._next] = path
            return self._next

    def leave(self, ticket):
        with self._cond:
            self._running.pop(ticket, None)
            self._cond.notify_all()

    def open(self):
        with self._cond:
            self._open = True

    def close(self):
        with self._cond:
            self._open = False

    def drain(self, timeout=30):
        with self._cond:
            if not self._cond.wait_for(lambda: not self._running, timeout):
                raise AssertionError(
                    f"the test server was still answering after {timeout} s: "
                    + ", ".join(sorted(self._running.values())))


class _Body:
    """The answer's body, passed through. The request leaves the gate when
    the body has been sent or when the server closes it, whichever comes
    first (leaving twice is harmless): Django's server reads what is left
    of the request before it closes the body, and that read can fail when
    the browser has already gone, so close() alone could be missed."""

    def __init__(self, body, done):
        self._body, self._done = body, done

    def __iter__(self):
        try:
            yield from self._body
        finally:
            self._done()

    def close(self):
        try:
            close = getattr(self._body, "close", None)
            if close:
                close()
        finally:
            self._done()


def _gated(gate, app):
    def application(environ, start_response):
        ticket = gate.enter(environ.get("PATH_INFO", ""))
        if ticket is None:
            start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
            return [b"the test is cleaning up"]
        try:
            body = app(environ, start_response)
        except BaseException:
            gate.leave(ticket)
            raise
        return _Body(body, lambda: gate.leave(ticket))
    return application


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
        # The server answers through this class's own gate (_ServerGate).
        cls.server_gate = _ServerGate()
        cls.static_handler = partial(lambda gate, app: _gated(gate, StaticFilesHandler(app)), cls.server_gate)
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = _NoLocalAgentBrowser(cls.playwright.chromium.launch())
        except Exception as e:
            cls.playwright.stop()
            super().tearDownClass()
            raise SkipTest(f"Chromium is not available: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def wait_for_the_server(self):
        """Return once the server has finished every request it was sent.
        Call it after closing a browser context, before reading what the
        page's requests wrote."""
        self.server_gate.drain()

    def _fixture_teardown(self):
        # The cleanup empties every table: no request may be running under
        # it, and none may start (see _ServerGate).
        self.server_gate.close()
        try:
            self.server_gate.drain()
            super()._fixture_teardown()
        finally:
            self.server_gate.open()

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
            self.wait_for_the_server()
