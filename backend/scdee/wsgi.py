"""
WSGI config for scdee project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/

Used by gunicorn in production:
    gunicorn scdee.wsgi:application --bind 0.0.0.0:8000
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scdee.settings.dev")

application = get_wsgi_application()
