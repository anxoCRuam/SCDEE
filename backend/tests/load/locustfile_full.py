"""
Comprehensive Locust load profile for SCDEE (RNF-1, RNF-2).

Reads CSV files produced by ``manage.py seed_load_test_data``.
"""

from __future__ import annotations

import csv
import logging
import os
import random
import time
import uuid
from pathlib import Path

from locust import HttpUser, between, events, task

logger = logging.getLogger(__name__)

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
    b"\x89\x00\x00\x00\rIDATx\x9cc````\x00\x00\x00\x05\x00\x01]\xcc"
    b"\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _login(client, email, password):
    resp = client.post(
        "/api/v1/auth/login/", json={"email": email, "password": password}, name="auth/login"
    )
    if resp.status_code == 200:
        return resp.json()["access_token"]
    return None


def _read_csv(env_var):
    path = os.environ.get(env_var)
    if not path or not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ═══════════════════════════════════════════════════════════════
# Student
# ═══════════════════════════════════════════════════════════════


class StudentReviewUser(HttpUser):
    wait_time = between(3, 10)
    weight = 70
    _rows = _read_csv("SCDEE_LOAD_STUDENT_FILE")

    def on_start(self):
        if not self._rows:
            self.environment.runner.quit()
            return
        row = random.choice(self._rows)  # noqa: S311
        self.email = row["email"]
        self.password = row["password"]
        self.instance_id = row["instance_id"]
        self.exam_id = row["exam_id"]
        self.problem_id = row.get("problem_id", "")
        token = _login(self.client, self.email, self.password)
        if not token:
            self._auth = False
            return
        self.client.headers["Authorization"] = f"Bearer {token}"
        self._auth = True
        self._submitted_review = False

    @task(3)
    def profile(self):
        if self._auth:
            self.client.get("/api/v1/profile/", name="student/profile")

    @task(2)
    def notifications(self):
        if self._auth:
            self.client.get("/api/v1/notifications/", name="student/notifications")

    @task(2)
    def instance_detail(self):
        if self._auth:
            self.client.get(
                f"/api/v1/instances/{self.instance_id}/", name="student/instance/detail"
            )

    @task(2)
    def download_pdf(self):
        if self._auth:
            with self.client.get(
                f"/api/v1/instances/{self.instance_id}/download/",
                name="student/instance/download",
                stream=True,
                catch_response=True,
            ) as resp:
                for _ in resp.iter_content(chunk_size=65536):
                    pass
                if resp.status_code != 200:
                    resp.failure(f"HTTP {resp.status_code}")

    @task(1)
    def maybe_review(self):
        if not self._auth or random.random() > 0.2:
            return
        if self._submitted_review:  # ← ya enviada
            return
        self._submitted_review = True  # ← marcar antes de enviar
        self.client.post(
            f"/api/v1/instances/{self.instance_id}/review-requests/",
            json={"problems": [{"problem_id": self.problem_id, "message": "Review pls"}]},
            name="student/review-request",
        )


# ═══════════════════════════════════════════════════════════════
# Grader
# ═══════════════════════════════════════════════════════════════


class GraderUser(HttpUser):
    wait_time = between(1, 3)
    weight = 10
    _rows = _read_csv("SCDEE_LOAD_GRADER_FILE")

    def on_start(self):
        if not self._rows:
            self.environment.runner.quit()
            return
        row = random.choice(self._rows)  # noqa: S311
        self.email = row["email"]
        self.password = row["password"]
        self.instance_id = row["instance_id"]
        self.problem_id = row["problem_id"]
        self.exam_id = row.get("exam_id", "")
        token = _login(self.client, self.email, self.password)
        if not token:
            self._auth = False
            return
        self.client.headers["Authorization"] = f"Bearer {token}"
        self._auth = True

    @task(1)
    def my_tasks(self):
        if self._auth:
            self.client.get("/api/v1/my-tasks/", name="grader/my-tasks")

    @task(3)
    def instance_detail(self):
        """Consultar la instancia antes de anotar."""
        if self._auth:
            self.client.get(
                f"/api/v1/instances/{self.instance_id}/",
                name="grader/instance/detail",
            )

    @task(5)
    def annotate(self):
        if not self._auth:
            return
        self.client.post(
            f"/api/v1/instances/{self.instance_id}/annotations/",
            json={
                "annotation_type": "TEXT",
                "payload": f"Loadtest {uuid.uuid4().hex[:8]}",
                "page_number": 1,
                "x": 50,
                "y": 50,
            },
            name="grader/annotations/create",
        )


# ═══════════════════════════════════════════════════════════════
# Ingestion (SFTP)
# ═══════════════════════════════════════════════════════════════


class IngestionFromSFTPUser(HttpUser):
    wait_time = between(2, 5)
    weight = 5
    _dir = os.environ.get("SCDEE_LOAD_SFTP_DIR", "")

    def on_start(self):
        self._enabled = bool(self._dir) and Path(self._dir).is_dir()

    @task
    def drop(self):
        if not self._enabled:
            return
        fname = f"loadtest_{uuid.uuid4().hex[:12]}.png"
        target = Path(self._dir) / fname
        t0 = time.perf_counter()
        try:
            target.write_bytes(_PNG_1X1)
            elapsed = (time.perf_counter() - t0) * 1000
            events.request.fire(
                request_type="SFTP",
                name="ingest/drop_file",
                response_time=elapsed,
                response_length=len(_PNG_1X1),
                exception=None,
                context={},
            )
        except OSError as exc:
            events.request.fire(
                request_type="SFTP",
                name="ingest/drop_file",
                response_time=(time.perf_counter() - t0) * 1000,
                response_length=0,
                exception=exc,
                context={},
            )


# ═══════════════════════════════════════════════════════════════
# Manager publish
# ═══════════════════════════════════════════════════════════════


class ManagerPublishUser(HttpUser):
    wait_time = between(60, 120)
    weight = 1

    def on_start(self):
        self.email = os.environ.get("SCDEE_LOAD_MANAGER_EMAIL", "")
        self.password = os.environ.get("SCDEE_LOAD_MANAGER_PW", "")
        self.exam_id = os.environ.get("SCDEE_LOAD_EXAM_ID", "")
        if not (self.email and self.password and self.exam_id):
            self._auth = False
            return
        token = _login(self.client, self.email, self.password)
        if not token:
            self._auth = False
            return
        self.client.headers["Authorization"] = f"Bearer {token}"
        self._auth = True

    @task
    def publish(self):
        if self._auth:
            self.client.post(f"/api/v1/exams/{self.exam_id}/publish/", name="manager/publish")
