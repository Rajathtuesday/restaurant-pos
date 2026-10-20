from django.apps import AppConfig


class BoxConfig(AppConfig):
    name = "box"
    verbose_name = "Rasova box (offline edition)"

    def ready(self):
        from box import signals  # noqa: F401  (the licence check on new orders)
