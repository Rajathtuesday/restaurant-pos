"""
The fast set (core/fast_tests.py) stays what it says.

A label that no longer names a test module would fail the whole run the
day someone renames a file; a browser test or a thread-race test would
quietly push the set past its two minutes. Both are caught here.

Run: python manage.py test core.tests.test_fast_tests
"""
import importlib
import inspect
import unittest

from django.test import SimpleTestCase, TestCase, TransactionTestCase

from core import fast_tests
from core.tests.browser import BrowserTestCase


def test_classes(label):
    """The test classes a label names: every one in a module, or the one
    class a "module.Class" label points at."""
    try:
        module = importlib.import_module(label)
    except ModuleNotFoundError:
        module_name, _, class_name = label.rpartition(".")
        cls = getattr(importlib.import_module(module_name), class_name)
        return [cls] if inspect.isclass(cls) and issubclass(cls, unittest.TestCase) else []
    return [obj for _, obj in inspect.getmembers(module, inspect.isclass)
            if issubclass(obj, unittest.TestCase) and obj.__module__ == module.__name__]


class FastSetTest(SimpleTestCase):

    def test_every_label_is_a_test_module(self):
        for label in fast_tests.FAST:
            self.assertTrue(test_classes(label), f"{label} holds no tests")

    def test_no_label_is_listed_twice(self):
        self.assertEqual(len(fast_tests.FAST), len(set(fast_tests.FAST)))

    def test_no_browser_test_and_no_thread_race(self):
        """A browser test starts Chromium and a live server; a race test
        (TransactionTestCase) empties every table after each test. Both
        belong in the full run."""
        for label in fast_tests.FAST:
            for cls in test_classes(label):
                self.assertFalse(issubclass(cls, BrowserTestCase), f"{label}.{cls.__name__} is a browser test")
                self.assertFalse(_is_race(cls), f"{label}.{cls.__name__} resets the database after each test; "
                                                "list the module's other classes by name instead")

    def test_the_guards_the_set_exists_for_are_in_it(self):
        for label in ("orders.tests.test_tax_engine", "payments.tests", "core.tests.test_tenant_walk",
                      "orders.tests.test_order_request_id.OrderRequestIdTest",
                      "core.tests.test_frontend_hygiene", "core.tests.test_frontend_js"):
            self.assertIn(label, fast_tests.FAST)

    def test_it_runs_as_manage_py_test_with_the_storage_keys_blanked(self):
        argv = fast_tests.argv(["--keepdb"])
        self.assertEqual(argv[:3], ["manage.py", "test", "--noinput"])
        self.assertIn("--keepdb", argv)
        self.assertEqual(argv[argv.index("--parallel") + 1], "8")
        self.assertEqual(fast_tests.argv(["--parallel", "2"]).count("--parallel"), 1)
        self.assertEqual(argv[-len(fast_tests.FAST):], fast_tests.FAST)
        self.assertIn("AWS_ACCESS_KEY_ID", fast_tests.STORAGE_KEYS)
        self.assertIn("AWS_SECRET_ACCESS_KEY", fast_tests.STORAGE_KEYS)


def _is_race(cls):
    """A class that resets the database after each test, as a thread-race
    test must (django.test.TestCase is a TransactionTestCase too, but rolls
    back instead)."""
    return issubclass(cls, TransactionTestCase) and not issubclass(cls, TestCase)
