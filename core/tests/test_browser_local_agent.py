"""
A page that asks for the local print agent gets the same answer on every
machine in a browser test (21 Nov 2026).

Kitchen Stations asks ws://localhost:8765 on load whether the Rasova print
agent runs. No agent runs beside a test; Linux refuses at once and Chromium
logs the refusal as a console error, Windows retries past the page's own
timeout and logs nothing. Five browser tests passed on Windows and failed
on Linux for that alone. core.tests.browser now gives every test page a
stand-in for that socket (NO_LOCAL_AGENT): "not running", at once, with no
network and nothing logged.
Run: python manage.py test core.tests.test_browser_local_agent
"""
from django.test import override_settings

from core.tests.browser import HELPERS, BrowserTestCase

ASK_THE_AGENT = """
return await new Promise((resolve) => {
  const ws = new WebSocket("ws://localhost:8765");
  ws.onopen = () => resolve("open");
  ws.onerror = () => resolve("not running:" + ws.readyState);
  setTimeout(() => resolve("no answer"), 10000);
});
"""


@override_settings(ROOT_URLCONF="core.tests.browser_gate_urls")
class TheLocalAgentIsNotRunningTest(BrowserTestCase):

    def test_run_page_gets_not_running_at_once_and_nothing_logged(self):
        out = self.run_page("/page/", [("ask", ASK_THE_AGENT)])
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["results"]["ask"], "not running:3")

    def test_a_context_a_test_opens_itself_gets_it_too(self):
        context = self.browser.new_context()
        self.addCleanup(self.wait_for_the_server)
        self.addCleanup(context.close)
        context.add_init_script(HELPERS)
        page = context.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(self.live_server_url + "/page/", wait_until="load")
        self.assertEqual(page.evaluate("async () => {" + ASK_THE_AGENT + "}"), "not running:3")
        self.assertEqual(errors, [])

    def test_other_sockets_are_left_alone(self):
        out = self.run_page("/page/", [("kind", 'return WebSocket.OPEN === 1 && typeof WebSocket === "function";')])
        self.assertEqual(out["results"]["kind"], True)
