# tenants/apps.py
from django.apps import AppConfig


class TenantsConfig(AppConfig):
    name = 'tenants'

    def ready(self):
        import tenants.checks  # noqa: F401 -- registers the check via @register()
