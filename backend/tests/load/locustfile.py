"""
Locust load profile for the SCDEE API (RNF-2 + Phase 12.6).

Three scenarios that exercise the load envelopes called out in RNF-2:

* ``PublishGradesUser`` — simulates the spike that follows a grade
  publication for a typical four-subject / 300-student deployment.
  Each user repeatedly fetches their own profile and notification feed.
* ``GraderUser`` — emulates a corrector hitting ``PUT /grades/``
  endpoints. Validates the optimistic-concurrency path under load.
* ``IngestionUser`` — uploads a tiny PNG via the manual ingest endpoint
  to drive the recognition queue.

Usage::

    pip install locust
    export SCDEE_LOAD_EMAIL=manager@org.local
    export SCDEE_LOAD_PASSWORD=MgrPass123!
    locust -f tests/load/locustfile.py --host=http://localhost:8000 \
           --headless -u 50 -r 5 -t 1m

Tag a scenario with ``--tags publish`` (or ``grade`` / ``ingest``) to
focus on a single user class.

The runner does not bring up the API or seed data; you are expected
to point at a running deployment with at least one organisation, one
manager account, one published exam, and a known instance/problem
PK pair (set ``SCDEE_INSTANCE_ID`` and ``SCDEE_PROBLEM_ID``).
"""

from __future__ import annotations

import io
import os
import random

from locust import HttpUser, between, tag, task


def _login(client) -> str | None:
    email = os.environ.get("SCDEE_LOAD_EMAIL", "manager@org.local")
    password = os.environ.get("SCDEE_LOAD_PASSWORD", "MgrPass123!")
    resp = client.post(
        "/api/v1/auth/login/",
        json={"email": email, "password": password},
        name="auth/login",
    )
    if resp.status_code != 200:
        return None
    return resp.json().get("access_token")


class PublishGradesUser(HttpUser):
    """Read-heavy spike from students checking their newly-published grade."""

    wait_time = between(0.5, 2.0)
    weight = 4

    def on_start(self):
        self.token = _login(self.client)
        if self.token:
            self.client.headers["Authorization"] = f"Bearer {self.token}"

    @tag("publish")
    @task(3)
    def get_profile(self):
        self.client.get("/api/v1/profile/", name="profile")

    @tag("publish")
    @task(2)
    def list_notifications(self):
        self.client.get("/api/v1/notifications/", name="notifications/list")


class GraderUser(HttpUser):
    """Steady-state grading load (~30 graders/org per RNF-2)."""

    wait_time = between(1.0, 3.0)
    weight = 1

    def on_start(self):
        self.token = _login(self.client)
        if self.token:
            self.client.headers["Authorization"] = f"Bearer {self.token}"
        self.instance_id = os.environ.get("SCDEE_INSTANCE_ID", "")
        self.problem_id = os.environ.get("SCDEE_PROBLEM_ID", "")

    @tag("grade")
    @task
    def put_grade(self):
        if not (self.instance_id and self.problem_id):
            return
        # Read first to capture the live version (optimistic concurrency).
        view = self.client.get(f"/api/v1/instances/{self.instance_id}/", name="instance/detail")
        if view.status_code != 200:
            return
        version = view.json().get("version", 1)
        self.client.put(
            f"/api/v1/instances/{self.instance_id}/problems/{self.problem_id}/grade/",
            json={"score": round(random.uniform(0, 10), 2), "version": version},  # noqa: S311
            name="grade/manual",
        )


class IngestionUser(HttpUser):
    """Drives the recognition queue from the manual ingest endpoint."""

    wait_time = between(2.0, 5.0)
    weight = 1

    def on_start(self):
        self.token = _login(self.client)
        if self.token:
            self.client.headers["Authorization"] = f"Bearer {self.token}"
        # 1×1 transparent PNG (smallest valid file) so we hit the pipeline
        # without consuming bandwidth.
        self.png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
            b"\x89\x00\x00\x00\rIDATx\x9cc````\x00\x00\x00\x05\x00\x01]\xcc"
            b"\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    @tag("ingest")
    @task
    def post_page(self):
        files = {"file": ("page.png", io.BytesIO(self.png), "image/png")}
        self.client.post("/api/v1/recognition/ingest/", files=files, name="recognition/ingest")
