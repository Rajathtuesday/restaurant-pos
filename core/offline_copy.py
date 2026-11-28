"""
What the service worker (static/sw.js) may keep for offline use, as the
server says it on each response, in one header, X-Rasova-Offline:

  never  a page it must not keep (the admin's, core.middleware)
  keep   a script's answer it may keep and hand back while the connection
         is down (a dish's options, so billing can still add a dish with
         options offline)

A page (a navigation) is kept unless it says never: offline billing needs
the pages. A script's answer is kept only when it says keep. The kitchen
tickets, the floor, an order, today's numbers are live: the worker used to
keep every answer and hand the old copy back when a read failed, so a
kitchen board could show tickets from whenever the copy was kept with no
"Sync Failed", and a read with no copy got the billing page instead
(28 Nov 2026). An old copy shown as live is worse than "check the
connection".
"""

HEADER = "X-Rasova-Offline"
NEVER = "never"
KEEP = "keep"


def keep_offline(response):
    """Let the service worker keep this script answer for offline use."""
    response[HEADER] = KEEP
    return response
