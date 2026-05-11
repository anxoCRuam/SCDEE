# backend/apps/audit/urls.py
from django.urls import path

from apps.audit.views.auditlog import AuditLogListView

urlpatterns = [
    path("", AuditLogListView.as_view(), name="audit-log-list"),
]
