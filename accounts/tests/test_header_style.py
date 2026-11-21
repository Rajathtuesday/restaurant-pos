"""
Each person's header style (21 Nov 2026): accounts/views/preference_views.py.
Run: python manage.py test accounts.tests.test_header_style
"""
import json

from django.test import Client, TestCase

from accounts.models import User
from core.tests.test_tenant_walk import Restaurant


class HeaderStyleTest(TestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "hdr-style")
        self.client = Client()

    def save(self, style, user=None):
        if user:
            self.client.force_login(user)
        return self.client.post("/account/header-style/", data=json.dumps({"style": style}), content_type="application/json")

    def test_fit_the_screen_by_default(self):
        self.assertEqual(self.r.owner.header_style, "auto")

    def test_a_person_saves_their_own_style_and_no_one_elses(self):
        for style in ("icons", "words", "both", "auto"):
            resp = self.save(style, user=self.r.staff)
            self.assertEqual(resp.status_code, 200, resp.content)
            self.assertEqual(User.objects.get(pk=self.r.staff.pk).header_style, style)
        self.assertEqual(User.objects.get(pk=self.r.owner.pk).header_style, "auto")

    def test_an_unknown_style_is_refused(self):
        resp = self.save("sparkly", user=self.r.staff)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(User.objects.get(pk=self.r.staff.pk).header_style, "auto")

    def test_no_login_no_saving(self):
        resp = self.save("icons")
        self.assertIn(resp.status_code, (302, 403))

    def test_the_header_carries_the_persons_style(self):
        User.objects.filter(pk=self.r.owner.pk).update(header_style="words")
        self.client.force_login(self.r.owner)
        page = self.client.get("/tables/").content.decode()
        self.assertIn('data-header-style="words"', page)
        self.assertIn('data-header-style-url="/account/header-style/"', page)
