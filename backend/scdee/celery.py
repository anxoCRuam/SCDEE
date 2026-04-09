"""
Celery application configuration for SCDEE.

This module creates the Celery app instance and configures it
to read settings from Django's settings module. It also enables
automatic discovery of task modules in all installed apps.

Queue architecture:
    - default:       General-purpose tasks
    - ocr:           CPU-intensive OCR processing (RF-9.4)
    - notifications:  Email sending and notification dispatch (RF-13.2)

Workers can be started per-queue for workload isolation:
    celery -A scdee worker -Q default -c 4
    celery -A scdee worker -Q ocr -c 2
    celery -A scdee worker -Q notifications -c 2
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scdee.settings.dev")

app = Celery("scdee")

# Read Celery config from Django settings, using the CELERY_ prefix.
# e.g. CELERY_BROKER_URL in settings becomes broker_url in Celery.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py modules in all INSTALLED_APPS.
# Each app can define tasks in apps/<name>/tasks.py and they'll
# be registered automatically.
app.autodiscover_tasks()
