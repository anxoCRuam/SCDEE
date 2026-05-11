"""
Integration tests for notifications (RF-13).

Covers: creation with email mirror, listing with filters,
mark read (individual + bulk), unread count, archive by course.
"""

import uuid
from unittest.mock import patch

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.courses.models.courses import AcademicCourse
from apps.notifications.models.notifications import (
    Notification,
    NotificationStatus,
    NotificationType,
)
from apps.notifications.services.notifications import (
    archive_by_course,
    create_notification,
)
from apps.organizations.models.organization import Organization

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


def _setup():
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    org = Organization.objects.create(name="Org", subdomain=f"o-{uuid.uuid4().hex[:8]}")
    user = user_model.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:8]}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="Test",
        last_name="User",
        organization=org,
    )
    return {"org": org, "user": user}


def _auth(user, pw="Pass123!"):
    reset_auth_plugin()
    c = APIClient()
    r = c.post("/api/v1/auth/login/", {"email": user.email, "password": pw}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access_token']}")
    return c


# ── Service tests ────────────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestNotificationService(TestCase):
    @patch("apps.notifications.services.notifications._enqueue_email_mirror")
    def test_create_notification(self, mock_email):
        ctx = _setup()
        notif = create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GRADES_PUBLISHED,
            title="Notas publicadas",
            message="Las notas han sido publicadas.",
        )
        assert notif.pk is not None
        assert notif.status == NotificationStatus.UNREAD

    @patch("apps.notifications.services.notifications._enqueue_email_mirror")
    def test_email_mirror_enqueued(self, mock_email):
        ctx = _setup()
        create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GENERAL,
            title="Test",
            message="Test message",
        )
        mock_email.assert_called_once()

    @patch("apps.notifications.services.notifications._enqueue_email_mirror")
    def test_email_not_sent_if_disabled(self, mock_email):
        ctx = _setup()
        ctx["user"].email_notifications_enabled = False
        ctx["user"].save()

        create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GENERAL,
            title="Test",
            message="Test",
        )
        mock_email.assert_not_called()

    @patch("apps.notifications.services.notifications._enqueue_email_mirror")
    def test_archive_by_course(self, mock_email):
        ctx = _setup()
        course = AcademicCourse(organization=ctx["org"], label="2024", is_active=False)
        course.save()

        create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GENERAL,
            title="Old",
            message="Old notification",
            course=course,
        )

        count = archive_by_course(course)
        assert count == 1

        notif = Notification.objects.first()
        assert notif.status == NotificationStatus.ARCHIVED


# ── API tests ────────────────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestNotificationAPI(TestCase):
    @patch("apps.notifications.services.notifications._enqueue_email_mirror")
    def test_list_notifications(self, mock_email):
        ctx = _setup()
        create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GENERAL,
            title="N1",
            message="Msg1",
        )
        create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GENERAL,
            title="N2",
            message="Msg2",
        )

        client = _auth(ctx["user"])
        resp = client.get("/api/v1/notifications/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["count"] == 2

    @patch("apps.notifications.services.notifications._enqueue_email_mirror")
    def test_filter_by_status(self, mock_email):
        ctx = _setup()
        n = create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GENERAL,
            title="Read",
            message="Already read",
        )
        n.status = NotificationStatus.READ
        n.save()

        create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GENERAL,
            title="Unread",
            message="Still unread",
        )

        client = _auth(ctx["user"])
        resp = client.get("/api/v1/notifications/?status=UNREAD")
        assert resp.data["count"] == 1

    @patch("apps.notifications.services.notifications._enqueue_email_mirror")
    def test_mark_read_individual(self, mock_email):
        ctx = _setup()
        n = create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GENERAL,
            title="T",
            message="M",
        )

        client = _auth(ctx["user"])
        resp = client.patch(
            "/api/v1/notifications/read/",
            {"notification_ids": [str(n.pk)]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["marked_read"] == 1

        n.refresh_from_db()
        assert n.status == NotificationStatus.READ

    @patch("apps.notifications.services.notifications._enqueue_email_mirror")
    def test_mark_all_read(self, mock_email):
        ctx = _setup()
        for i in range(5):
            create_notification(
                user=ctx["user"],
                notification_type=NotificationType.GENERAL,
                title=f"N{i}",
                message=f"M{i}",
            )

        client = _auth(ctx["user"])
        resp = client.patch("/api/v1/notifications/read/", {}, format="json")
        assert resp.data["marked_read"] == 5

    @patch("apps.notifications.services.notifications._enqueue_email_mirror")
    def test_unread_count(self, mock_email):
        ctx = _setup()
        for i in range(3):
            create_notification(
                user=ctx["user"],
                notification_type=NotificationType.GENERAL,
                title=f"N{i}",
                message=f"M{i}",
            )

        client = _auth(ctx["user"])
        resp = client.get("/api/v1/notifications/count/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["unread_count"] == 3

    @patch("apps.notifications.services.notifications._enqueue_email_mirror")
    def test_archived_excluded_by_default(self, mock_email):
        ctx = _setup()
        n = create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GENERAL,
            title="Archived",
            message="Old",
        )
        n.status = NotificationStatus.ARCHIVED
        n.save()

        create_notification(
            user=ctx["user"],
            notification_type=NotificationType.GENERAL,
            title="Active",
            message="New",
        )

        client = _auth(ctx["user"])
        resp = client.get("/api/v1/notifications/")
        assert resp.data["count"] == 1  # Only active, not archived
