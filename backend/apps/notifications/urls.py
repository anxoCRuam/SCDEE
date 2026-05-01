"""
URL patterns for notification endpoints.

Mounted at: /api/v1/notifications/
"""

from django.urls import path

from apps.notifications.views import MarkReadView, NotificationListView, UnreadCountView

app_name = "notifications"

urlpatterns = [
    path("", NotificationListView.as_view(), name="notification-list"),
    path("read/", MarkReadView.as_view(), name="notification-read"),
    path("count/", UnreadCountView.as_view(), name="notification-count"),
]
