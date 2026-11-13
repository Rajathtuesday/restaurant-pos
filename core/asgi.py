"""
ASGI config for core project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

from core.db_limits import ask_for_web_limits

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
# Web requests get a statement timeout; migrate and Celery do not.
ask_for_web_limits(os.environ)

application = get_asgi_application()
