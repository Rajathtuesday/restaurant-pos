# tenants/apps.py
import sys
import logging

from django.apps import AppConfig

logger = logging.getLogger("pos.tenants")

# Rendered with an empty context at worker boot to force Django's cached
# template loader to compile these (and their {% extends %} parents) before
# any real request pays for it -- see _warm_templates() below.
_CRITICAL_TEMPLATES = [
    "tokens/token_billing.html",
    "menu/digital_menu.html",
    "orders/tables.html",
    "orders/billing.html",
]


def _warm_templates():
    from django.template.loader import render_to_string

    for name in _CRITICAL_TEMPLATES:
        try:
            render_to_string(name, {})
        except Exception:
            # Never let a template warm-up failure block app startup --
            # worst case is falling back to today's cold-first-request cost.
            logger.warning("Template warm-up failed for %s", name, exc_info=True)


class TenantsConfig(AppConfig):
    name = 'tenants'

    def ready(self):
        import tenants.checks  # noqa: F401 -- registers the check via @register()

        # Only warm templates for the actual gunicorn server process --
        # ready() also fires for every `manage.py` invocation (migrate,
        # test, shell, ...), where this would just be wasted, pointless
        # latency since none of those serve real requests.
        if 'gunicorn' in sys.argv[0]:
            _warm_templates()
