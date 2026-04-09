"""
SCDEE Django project package.

Importing the Celery app here ensures it's loaded when Django starts,
so that @shared_task decorators in app modules are auto-discovered.
"""

from .celery import app as celery_app

__all__ = ["celery_app"]
