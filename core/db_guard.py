from django.core.exceptions import ImproperlyConfigured


def require_postgres_in_production(engine, debug, allow_other=False):
    """
    Refuse to start a production process (DEBUG off) on anything but
    PostgreSQL. settings.py falls back to SQLite when DB_ENGINE is missing,
    which is handy locally but in production would quietly boot on an empty
    file next to the code: orders written where nothing backs them up, and
    no error anywhere. ALLOW_NON_POSTGRES=1 is the deliberate escape hatch.
    """
    if debug or allow_other or "postgresql" in (engine or ""):
        return
    raise ImproperlyConfigured(
        f"DB_ENGINE is {engine!r} but DEBUG is off. Production must run on PostgreSQL: "
        "set DB_ENGINE=django.db.backends.postgresql (and DB_NAME, DB_USER, DB_PASSWORD, DB_HOST) "
        "in .env. Set ALLOW_NON_POSTGRES=1 only if you really mean to run without it."
    )
