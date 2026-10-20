"""
Which edition of Rasova this is: the cloud (rasova.net, the default) or a
box (the offline edition: Rasova running on a small computer inside one
restaurant, reached over the restaurant's own Wi-Fi, with no internet
needed). settings.BOX comes from the environment: RASOVA_EDITION=box.

On a box, everything that needs the internet or a guest's phone is off:
the guest QR menu (guests aren't on the restaurant's network), guest
waiter calls and feedback, Zomato/Swiggy, Razorpay, WhatsApp bills, AI menu
import. The pages that serve them are not served at all (OFF_PATHS), so an
old printed QR code or a bookmarked link gets a plain 404.
"""
import re

from django.conf import settings


def is_box():
    return bool(getattr(settings, "BOX", False))


BOX_OFF_FEATURES = frozenset({
    "qr_menu", "waiter_call", "guest_feedback",          # guests' phones are not on the box's network
    "platform_sync",                                      # Zomato / Swiggy
    "razorpay_gateway", "whatsapp_receipts",              # need the internet
    "ai_menu_import", "ai_recipe_import",                 # need the internet
})

OFF_PATHS = tuple(re.compile(p) for p in (
    r"^/demo/", r"^/live-demo/", r"^/compare/", r"^/(fine-dining|cafe|qsr|bar)-pos/", r"^/sitemap\.xml$",
    r"^/superuser/",                                      # the platform's panel: there is one restaurant here
    r"^/agent/",                                          # the cloud's print agent installer
    r"^/api/aggregator/", r"^/api/razorpay/", r"^/razorpay/", r"^/billing/webhook/",
    r"^/bill/public/",                                    # WhatsApp bill links
    r"^/menu/order-status/", r"^/menu/call-waiter/", r"^/menu/[0-9a-fA-F-]{36}/", r"^/menu/ai-",
))


def is_off_path(path):
    return any(p.match(path) for p in OFF_PATHS)
