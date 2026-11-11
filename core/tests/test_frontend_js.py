"""
The page scripts' own tests, run through node from the Django suite so one
`manage.py test` covers everything.

static/js/tests/*.test.mjs test the modules in static/js (the server
client, the offline queues, platform detection, the CSRF and page-data
readers) with fakes, in node's built-in runner. This drives that runner
and fails with its output if any of them fails. Locally without node the
test is skipped; in CI (CI=true) a missing node is a failure, the same
rule as orders/tests/test_cart_tax_js.py.

Run: python manage.py test core.tests.test_frontend_js
     node --test static/js/tests/          (the same thing, directly)
"""
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from orders.tests.test_cart_tax_js import NODE, _needs_node

TESTS_DIR = Path(settings.BASE_DIR) / "static" / "js" / "tests"


class FrontendModuleTests(SimpleTestCase):
    def test_the_module_tests_pass(self):
        _needs_node(self)
        # node expands the glob itself; a bare directory is not accepted. A
        # test that never settles (a queue waiting on a promise nobody
        # resolves) is a failure after 20 seconds, not a hung build.
        # TAP output whatever the terminal: node picks a prettier reporter
        # on its own when it sees a TTY.
        done = subprocess.run(
            [NODE, "--test", "--test-timeout=20000", "--test-reporter=tap", "--test-reporter-destination=stdout",
             "static/js/tests/*.test.mjs"],
            capture_output=True, text=True, timeout=300, cwd=settings.BASE_DIR,
        )
        self.assertEqual(
            done.returncode, 0,
            "node --test failed:\n" + done.stdout[-6000:] + "\n" + done.stderr[-3000:],
        )
        self.assertIn("# fail 0", done.stdout)
        self.assertNotIn("# pass 0\n", done.stdout, "no tests ran: is the tests directory empty?")
