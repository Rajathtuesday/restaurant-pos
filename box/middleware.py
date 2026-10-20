"""
What a box does on every request (nothing at all in the cloud edition):

  * pages a box never serves (box/edition.py OFF_PATHS) answer a plain 404;
  * "/" goes to the dashboard (or the login), not the marketing site;
  * until the restaurant has been set up, every page goes to /box/setup/;
  * the clock is noted (box/clock.py), at most once a minute.

It runs early, before WhiteNoise (which serves the marketing site's files)
and before the login is read, so its 404 is a plain one: Django's error page
would look for a logged-in user that isn't there yet.
"""
from django.core.cache import cache
from django.http import HttpResponseNotFound
from django.shortcuts import redirect

from box.edition import is_box, is_off_path

SETUP_PATH = "/box/setup/"
ALWAYS_OPEN = ("/static/", "/health/", "/favicon.ico", SETUP_PATH)


def restaurant_exists():
    if cache.get("box_setup_done"):
        return True
    from tenants.models import Tenant
    done = Tenant.objects.exists()
    if done:
        cache.set("box_setup_done", True, None)
    return done


class BoxMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not is_box():
            return self.get_response(request)
        path = request.path
        if is_off_path(path):
            return HttpResponseNotFound("Not on a Rasova box.", content_type="text/plain")
        if not path.startswith(ALWAYS_OPEN) and not restaurant_exists():
            return redirect(SETUP_PATH)
        if path == "/":
            return redirect("/dashboard/")          # which sends anyone not logged in to the login
        from box import clock
        clock.observe()
        return self.get_response(request)
