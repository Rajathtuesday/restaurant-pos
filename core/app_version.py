"""
Asking an old Android app to update (B16, 13 Nov 2026).

An old version of the Rasova app is a quiet failure: it stops printing,
and the restaurant says "Rasova does not print". The app names its version
in every request ("... RasovaPOS-Android/1.2.1", rasova_android/AppUserAgent.kt);
when it is older than MINIMUM_ANDROID_APP, every page shows a banner that
says so and how to update (templates/core/_app_update_banner.html, drawn by
the server, so it shows even if the page's own scripts fail).

Every app before 1.2.1 reports "1.0" whatever it really is. So raising the
minimum to 1.2.1 asks every older app to update; do that only once there is
a newer app to get.

Where to get it: a Rasova box serves the app itself (Connect a device,
/android/rasova.apk). rasova.net no longer does: on 27 Nov 2026 the app
download came off the site and work on the Android app paused, so in the
cloud the banner says to use Rasova in the phone's browser instead.
"""
import re

# The oldest app version the server still works with. "1.0" asks no one.
MINIMUM_ANDROID_APP = "1.0"
BOX_DOWNLOAD = "/android/rasova.apk"


def download():
    """Where an out-of-date app gets the new one, or None (rasova.net)."""
    from box.edition import is_box
    return BOX_DOWNLOAD if is_box() else None

_TOKEN = re.compile(r"RasovaPOS-Android/([0-9][0-9A-Za-z._-]*)")


def app_version(user_agent):
    """The Rasova app's version from a User-Agent, or None when the request
    is not from the app."""
    found = _TOKEN.search(user_agent or "")
    return found.group(1) if found else None


def _parts(version):
    """'1.10.2-beta' -> (1, 10, 2): numbers compare as numbers, not text."""
    return tuple(int(n) for n in re.findall(r"\d+", version.split("-")[0])[:3]) or (0,)


def is_older(version, minimum):
    a, b = _parts(version), _parts(minimum)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) < b + (0,) * (width - len(b))


def app_update(request, minimum=None):
    """None, or what the banner needs: the app's version, the minimum and
    where to get the new one (None in the cloud)."""
    version = app_version(request.META.get("HTTP_USER_AGENT", ""))
    minimum = minimum or MINIMUM_ANDROID_APP
    if version is None or not is_older(version, minimum):
        return None
    return {"version": version, "minimum": minimum, "download": download()}
