"""
Celery – async task queue configuration (RNF-4).
"""

from ._paths import env

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://redis:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://redis:6379/2")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_QUEUES = {
    "default": {},
    "recognition": {},
    "notifications": {},
}
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

CELERY_BEAT_SCHEDULE = {
    "open-reviews": {
        "task": "apps.reviews.tasks.process_review_openings",
        "schedule": 60.0,
    },
    "close-reviews": {
        "task": "apps.reviews.tasks.process_review_closings",
        "schedule": 60.0,
    },
    "detect-missing-pages": {
        "task": "apps.ingestion.tasks.detect_missing_pages",
        "schedule": 300.0,
    },
    "auto-deletion": {
        "task": "apps.core.tasks.perform_auto_deletion",
        "schedule": 86400.0,
    },
    "deletion-notices": {
        "task": "apps.core.tasks.check_upcoming_deletions",
        "schedule": 86400.0,
    },
}
