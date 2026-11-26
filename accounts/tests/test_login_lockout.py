"""
The login lockout (accounts/lockout.py), through the real login page.

Before 26 Nov 2026 axes was LAST among the authentication backends, so a
right password was accepted before axes was asked: after 7 wrong guesses
("locked" from the 5th) the right one logged in. And it locked an address,
so after one waiter's 5 typos on a pub's Wi-Fi the next person's typo said
"locked". Now axes is first and locks a name at an address (5 wrong), and a
name from anywhere (20 wrong); the owner can Unlock on Setup > Staff.

Each phone here has its own fixed address (REMOTE_ADDR), the way it reaches
the server. Axes is off for the rest of the suite (client.login() calls
authenticate() with no request) and on here.

Run: python manage.py test accounts.tests.test_login_lockout
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from axes.models import AccessAttempt
from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.lockout import NAME_ANYWHERE_LIMIT, _opens_at, clock
from accounts.models import User
from core.testing import freeze_ratelimit_clock
from tenants.models import Outlet, Tenant

PUB_WIFI = "203.0.113.7"       # every phone in the pub reaches the cloud from here
HOME = "192.0.2.20"
CLOUDFLARE_EDGE = "162.158.10.20"
PASSWORD = "right-password-1"

LOCKED = "Too many wrong passwords for this name."
WRONG = "Invalid username or password."
TOO_FAST = "Too many tries from this network. Wait a minute."
AXES_OWN_WORDS = "Account locked"


def _attempt(username, password, address=PUB_WIFI, client=None):
    """One login from one phone. The response carries .logged_in."""
    client = client or Client(REMOTE_ADDR=address)
    resp = client.post(reverse("login"), {"username": username, "password": password})
    resp.logged_in = "_auth_user_id" in client.session
    return resp


def _via_cloudflare(visitor):
    """A phone as gunicorn sees it in production: nginx on 127.0.0.1 says
    Cloudflare's edge connected, and Cloudflare says who the visitor is."""
    return Client(REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP=CLOUDFLARE_EDGE, HTTP_CF_CONNECTING_IP=visitor)


def _opens(username):
    """The time a lock made at one address opens, as the pages print it:
    an hour after its last wrong try."""
    row = AccessAttempt.objects.filter(username=username).latest("attempt_time")
    return clock(row.attempt_time + timedelta(hours=settings.AXES_COOLOFF_TIME))


@override_settings(AXES_ENABLED=True)
class _Lockout(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.tenant = Tenant.objects.create(name="Lock Pub", slug="lock-pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = self._user("pub_owner", "owner")
        self.waiter_a = self._user("waiter_a", "waiter")
        self.waiter_b = self._user("waiter_b", "waiter")

    def _user(self, username, role, tenant=None, outlet=None):
        return User.objects.create_user(
            username=username, password=PASSWORD, role=role,
            tenant=tenant or self.tenant, outlet=outlet or self.outlet,
        )

    def _wrong(self, username="waiter_a", address=PUB_WIFI, client_for=None):
        """5 wrong passwords for `username` from one address; the last answer."""
        for n in range(settings.AXES_FAILURE_LIMIT):
            client = client_for(address) if client_for else None
            resp = _attempt(username, f"typo{n}", address, client)
        return resp

    def _lock(self, username="waiter_a", address=PUB_WIFI, client_for=None):
        """5 wrong passwords, and the 5th is already refused as locked."""
        self.assertLocked(self._wrong(username, address, client_for))

    def assertLocked(self, resp):
        self.assertFalse(resp.logged_in)
        self.assertContains(resp, LOCKED, status_code=429)
        self.assertContains(resp, "or ask the owner to unlock it.", status_code=429)
        self.assertNotContains(resp, AXES_OWN_WORDS, status_code=429)
        self.assertContains(resp, 'name="password"', status_code=429)   # Rasova's own login page

    def _as(self, user):
        client = Client()
        client.force_login(user)
        return client


class NameAtAddressTest(_Lockout):
    """Rule 1: 5 wrong passwords lock that name at that address, nobody else."""

    def test_the_right_password_after_five_wrong_is_refused(self):
        self._wrong()
        resp = _attempt("waiter_a", PASSWORD)
        self.assertFalse(resp.logged_in, "the right password got past the lock")
        self.assertLocked(resp)

    def test_someone_else_on_the_same_wifi_logs_in(self):
        self._lock()
        self.assertTrue(_attempt("waiter_b", PASSWORD).logged_in)

    def test_someone_elses_typo_on_the_same_wifi_says_wrong_password(self):
        self._wrong()
        resp = _attempt("waiter_b", "typo")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, WRONG)
        self.assertNotContains(resp, LOCKED)

    def test_the_same_name_from_another_address_logs_in(self):
        self._lock()
        self.assertTrue(_attempt("waiter_a", PASSWORD, HOME).logged_in)
        self.assertLocked(_attempt("waiter_a", PASSWORD))    # still locked at the pub

    def test_the_page_says_when_the_lock_opens(self):
        self._lock()
        resp = _attempt("waiter_a", PASSWORD)
        self.assertContains(
            resp, f"Try again after {_opens('waiter_a')}, or ask the owner to unlock it.", status_code=429,
        )

    def test_trying_while_locked_does_not_move_the_time(self):
        self._lock()
        before = AccessAttempt.objects.get(username="waiter_a")
        self.assertLocked(_attempt("waiter_a", PASSWORD))
        self.assertLocked(_attempt("waiter_a", "typo"))
        after = AccessAttempt.objects.get(username="waiter_a")
        self.assertEqual(
            (after.failures_since_start, after.attempt_time), (before.failures_since_start, before.attempt_time),
        )

    def test_the_lock_opens_after_the_hour(self):
        self._lock()
        AccessAttempt.objects.filter(username="waiter_a").update(attempt_time=F("attempt_time") - timedelta(minutes=61))
        self.assertTrue(_attempt("waiter_a", PASSWORD).logged_in)

    def test_four_typos_then_the_right_password_starts_the_count_again(self):
        for n in range(4):
            _attempt("waiter_a", f"typo{n}")
        self.assertTrue(_attempt("waiter_a", PASSWORD).logged_in)
        for n in range(4):
            resp = _attempt("waiter_a", f"typo{n}")
        self.assertContains(resp, WRONG)


class NameFromAnywhereTest(_Lockout):
    """Rule 2: 20 wrong passwords for one name, from any mix of addresses."""

    def test_twenty_wrong_from_twenty_addresses_lock_the_name_everywhere(self):
        for n in range(NAME_ANYWHERE_LIMIT):
            resp = _attempt("waiter_a", f"guess{n}", f"198.51.100.{n}")
        self.assertLocked(resp)                                     # the 20th itself
        self.assertLocked(_attempt("waiter_a", PASSWORD, "198.51.100.200"))
        self.assertLocked(_attempt("waiter_a", PASSWORD, PUB_WIFI))
        self.assertTrue(_attempt("waiter_b", PASSWORD, "198.51.100.200").logged_in)

    def test_nineteen_from_anywhere_do_not(self):
        for n in range(NAME_ANYWHERE_LIMIT - 1):
            _attempt("waiter_a", f"guess{n}", f"198.51.100.{n}")
        self.assertTrue(_attempt("waiter_a", PASSWORD, "198.51.100.200").logged_in)

    def test_a_guesser_moving_on_after_each_lock_is_stopped_at_twenty(self):
        for address in ("198.51.100.1", "198.51.100.2", "198.51.100.3", "198.51.100.4"):
            self._lock("waiter_a", address)
        self.assertLocked(_attempt("waiter_a", PASSWORD, "198.51.100.5"))


@override_settings(RATELIMIT_ENABLE=True)
class LoginRateLimitTest(_Lockout):
    """10 tries a minute for one name from one address; 60 for one address."""

    def setUp(self):
        super().setUp()
        freeze_ratelimit_clock(self)

    def test_a_shift_change_on_one_wifi_all_log_in(self):
        names = [self._user(f"shift_{n}", "waiter").username for n in range(12)]
        for name in names:
            self.assertTrue(_attempt(name, PASSWORD).logged_in, name)

    def test_eleven_tries_for_one_name_from_one_address_are_slowed(self):
        for _ in range(10):
            self.assertTrue(_attempt("waiter_a", PASSWORD).logged_in)
        resp = _attempt("waiter_a", PASSWORD)
        self.assertFalse(resp.logged_in)
        self.assertContains(resp, TOO_FAST, status_code=429)
        self.assertTrue(_attempt("waiter_b", PASSWORD).logged_in)  # the next person is not slowed

    def test_many_names_from_one_address_meet_the_ceiling(self):
        for n in range(60):
            self.assertNotEqual(_attempt(f"nobody_{n}", "guess").status_code, 429, n)
        resp = _attempt("waiter_b", PASSWORD)
        self.assertFalse(resp.logged_in)
        self.assertContains(resp, TOO_FAST, status_code=429)
        self.assertTrue(_attempt("waiter_b", PASSWORD, HOME).logged_in)  # another address is not


class UnlockTest(_Lockout):
    """Setup > Staff > Unlock: the same people and rules as the password reset."""

    def setUp(self):
        super().setUp()
        self.manager = self._user("pub_manager", "manager")
        self.cashier = self._user("pub_cashier", "cashier")

    def _unlock(self, as_user, target):
        return self._as(as_user).post(reverse("unlock_staff_login", args=[target.id]))

    def test_the_owner_unlocks_and_the_waiter_logs_in_at_once(self):
        self._lock()
        resp = self._unlock(self.owner, self.waiter_a)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["message"], "waiter_a can log in now.")
        self.assertTrue(_attempt("waiter_a", PASSWORD).logged_in)      # same password

    def test_unlock_opens_the_from_anywhere_lock_too(self):
        for n in range(NAME_ANYWHERE_LIMIT):
            _attempt("waiter_a", f"guess{n}", f"198.51.100.{n}")
        self.assertEqual(self._unlock(self.owner, self.waiter_a).status_code, 200)
        self.assertTrue(_attempt("waiter_a", PASSWORD, "198.51.100.200").logged_in)

    def test_a_manager_unlocks_staff_of_their_outlet(self):
        self._lock()
        self.assertEqual(self._unlock(self.manager, self.waiter_a).status_code, 200)
        self.assertTrue(_attempt("waiter_a", PASSWORD).logged_in)

    def test_a_cashier_cannot(self):
        self._lock()
        self.assertEqual(self._unlock(self.cashier, self.waiter_a).status_code, 403)
        self.assertLocked(_attempt("waiter_a", PASSWORD))

    def test_another_restaurants_staff_is_not_found(self):
        other = Tenant.objects.create(name="Other Pub", slug="lock-other")
        stranger = self._user("stranger", "waiter", other, Outlet.objects.create(tenant=other, name="Other"))
        self._lock("stranger")
        self.assertEqual(self._unlock(self.owner, stranger).status_code, 404)
        self.assertLocked(_attempt("stranger", PASSWORD))

    def test_an_owner_account_cannot_be_unlocked_here(self):
        self._lock("pub_owner")
        self.assertEqual(self._unlock(self.manager, self.owner).status_code, 403)
        self.assertLocked(_attempt("pub_owner", PASSWORD))

    def test_unlock_is_post_only(self):
        resp = self._as(self.owner).get(reverse("unlock_staff_login", args=[self.waiter_a.id]))
        self.assertEqual(resp.status_code, 405)

    def test_a_password_reset_still_unlocks(self):
        self._lock()
        resp = self._as(self.owner).post(
            reverse("reset_staff_password", args=[self.waiter_a.id]),
            data=json.dumps({"password": "a-new-password"}), content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(_attempt("waiter_a", "a-new-password").logged_in)


class StaffPageTest(_Lockout):
    def test_a_locked_person_shows_when_it_opens_and_unlock(self):
        self._lock()
        resp = self._as(self.owner).get(reverse("setup_staff"))
        self.assertContains(resp, f'<span class="locked-badge" id="locked-{self.waiter_a.id}">Locked until {_opens("waiter_a")}</span>', html=False)
        self.assertContains(resp, reverse("unlock_staff_login", args=[self.waiter_a.id]))
        self.assertNotContains(resp, f'id="locked-{self.waiter_b.id}"')
        self.assertNotContains(resp, reverse("unlock_staff_login", args=[self.waiter_b.id]))

    def test_after_unlock_the_page_shows_no_lock(self):
        self._lock()
        owner = self._as(self.owner)
        owner.post(reverse("unlock_staff_login", args=[self.waiter_a.id]))
        resp = owner.get(reverse("setup_staff"))
        self.assertNotContains(resp, "Locked until")

    def test_a_name_locked_from_anywhere_shows_too(self):
        for n in range(NAME_ANYWHERE_LIMIT):
            _attempt("waiter_a", f"guess{n}", f"198.51.100.{n}")
        # One wrong try at each of 20 addresses: the lock opens when the
        # oldest leaves the hour and 19 are left.
        oldest = AccessAttempt.objects.filter(username="waiter_a").earliest("attempt_time").attempt_time
        resp = self._as(self.owner).get(reverse("setup_staff"))
        self.assertContains(resp, f"Locked until {clock(oldest + timedelta(hours=1))}")


class OtherWaysInTest(_Lockout):
    """The live demo's magic link logs in with login(), no password: a lock
    on the name (anyone can type demo_owner five times) never stops it."""

    def setUp(self):
        super().setUp()
        from orders.services.demo_seed import DEMO_OWNER_USERNAME
        demo = Tenant.objects.create(name="Demo Bistro", slug="lock-demo")
        self.demo_owner = User.objects.create_user(username=DEMO_OWNER_USERNAME, password=None, role="owner", tenant=demo)

    def _demo(self, address=PUB_WIFI):
        client = Client(REMOTE_ADDR=address)
        resp = client.get(reverse("live-demo"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(client.session.get("_auth_user_id"), str(self.demo_owner.pk))

    def test_the_live_demo_link_logs_in(self):
        self._demo()

    def test_the_live_demo_link_logs_in_while_its_name_is_locked(self):
        self._lock(self.demo_owner.username)
        self._demo()


class BehindTheProxiesTest(_Lockout):
    """In production every request reaches Django from nginx on 127.0.0.1;
    the lock must be on the visitor's address (core.utils.get_client_ip)."""

    def test_through_cloudflare_the_lock_is_on_the_visitor(self):
        self._lock(client_for=_via_cloudflare)
        self.assertEqual(set(AccessAttempt.objects.values_list("ip_address", flat=True)), {PUB_WIFI})
        self.assertLocked(_attempt("waiter_a", PASSWORD, client=_via_cloudflare(PUB_WIFI)))
        self.assertTrue(_attempt("waiter_a", PASSWORD, client=_via_cloudflare(HOME)).logged_in)

    def test_through_nginx_alone_the_lock_is_on_the_visitor(self):
        def via_nginx(visitor):
            return Client(REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP=visitor)
        self._lock(client_for=via_nginx)
        self.assertEqual(set(AccessAttempt.objects.values_list("ip_address", flat=True)), {PUB_WIFI})
        self.assertTrue(_attempt("waiter_a", PASSWORD, client=via_nginx(HOME)).logged_in)


class WhenALockOpensTest(SimpleTestCase):
    """accounts.lockout._opens_at: the newest tries stay longest, so a lock
    opens when the try that brought the newest up to the limit is an hour old."""

    def setUp(self):
        self.opens_at = _opens_at
        self.hour = timedelta(hours=1)
        self.now = timezone.now()

    def test_one_row_of_five(self):
        self.assertEqual(self.opens_at([(self.now, 5)], 5, self.hour), self.now + self.hour)

    def test_short_of_the_limit_is_no_lock(self):
        self.assertIsNone(self.opens_at([(self.now, 4)], 5, self.hour))

    def test_two_browsers_at_one_address_add_up(self):
        older, newer = self.now - timedelta(minutes=30), self.now
        self.assertEqual(self.opens_at([(older, 3), (newer, 2)], 5, self.hour), older + self.hour)

    def test_more_than_enough_new_tries_ignore_the_old_ones(self):
        older, newer = self.now - timedelta(minutes=30), self.now
        self.assertEqual(self.opens_at([(older, 3), (newer, 6)], 5, self.hour), newer + self.hour)

    def test_the_clock_rounds_up_to_the_minute(self):
        when = datetime(2026, 11, 26, 22, 41, 5, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertEqual(clock(when), "10:42 pm")
        self.assertEqual(clock(when.replace(minute=42, second=0)), "10:42 pm")


class SettingsHoldTest(SimpleTestCase):
    """So nobody flips them back: each one undone reopens a hole above."""

    def test_axes_is_asked_first(self):
        self.assertEqual(settings.AUTHENTICATION_BACKENDS[0], "axes.backends.AxesStandaloneBackend")
        self.assertIn("django.contrib.auth.backends.ModelBackend", settings.AUTHENTICATION_BACKENDS)

    def test_a_lock_is_a_name_at_an_address_or_a_name_from_anywhere(self):
        self.assertEqual(settings.AXES_LOCKOUT_PARAMETERS, [["username", "ip_address"]])
        self.assertEqual(settings.AXES_FAILURE_LIMIT, 5)
        self.assertEqual(settings.AXES_COOLOFF_TIME, 1)
        self.assertEqual(NAME_ANYWHERE_LIMIT, 20)
        self.assertEqual(settings.AXES_HANDLER, "accounts.lockout.RasovaAxesHandler")

    def test_rasova_says_it_and_a_retry_does_not_move_the_time(self):
        self.assertEqual(settings.AXES_LOCKOUT_CALLABLE, "accounts.lockout.lockout_response")
        self.assertFalse(settings.AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT)

    def test_axes_is_off_for_the_rest_of_the_suite(self):
        # client.login() calls authenticate() with no request, which axes
        # refuses from first place; the tests above switch it on.
        self.assertFalse(settings.AXES_ENABLED)
