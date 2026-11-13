"""
What keeps the test suite quick enough to run.

Why: on 13 November 2026 a test that made four users took 6 seconds, and
5.7 of them were password hashing. The production hasher (PBKDF2, about a
million rounds) is the right one for real passwords and the wrong one for
the thousands of users the suite makes. Some test files had switched it
off for themselves; most had not. It is now switched off for every test
run, in one place (core/settings.py, under _TESTING).

Run: python manage.py test core.tests.test_test_speed
"""
import time

from django.conf import settings
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from django.test import SimpleTestCase


class PasswordHashingInTestsTest(SimpleTestCase):

    def test_the_suite_uses_the_fast_hasher(self):
        self.assertEqual(settings.PASSWORD_HASHERS, ["django.contrib.auth.hashers.MD5PasswordHasher"])
        self.assertEqual(identify_hasher(make_password("1234")).algorithm, "md5")

    def test_a_password_still_checks_as_a_password(self):
        hashed = make_password("1234")
        self.assertTrue(check_password("1234", hashed))
        self.assertFalse(check_password("4321", hashed))

    def test_making_a_hundred_users_worth_of_passwords_takes_no_time(self):
        start = time.perf_counter()
        for i in range(100):
            make_password(f"pw{i}")
        self.assertLess(time.perf_counter() - start, 1.0)
