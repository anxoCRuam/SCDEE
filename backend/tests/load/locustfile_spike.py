"""
Fase 3 – Pico de estudiantes tras la publicación de notas (RNF-2).

Simula la avalancha de 1200 estudiantes que consultan su instancia,
descargan el PDF y (algunos) solicitan revisión. Un único manager
ejecuta la publicación al inicio de la prueba, disparando las
notificaciones. La rampa de 50 usuarios/segundo alcanza el pico en
menos de 30 segundos.

Uso:
    source /tmp/loadtest/manager.txt
    locust -f locustfile_spike.py --host=http://localhost:8000 \
        --headless -u 1200 -r 50 -t 5m --csv=/tmp/loadtest_phase3
"""

import csv
import os
import random
from pathlib import Path

from locust import HttpUser, between, task


def _login(client, email, pw):
    r = client.post(
        "/api/v1/auth/login/", json={"email": email, "password": pw}, name="auth/login"
    )
    return r.json()["access_token"] if r.status_code == 200 else None


def _read_csv(env_var):
    path = os.environ.get(env_var)
    if not path or not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


class StudentUser(HttpUser):
    wait_time = between(5, 10)
    weight = 95
    _rows = _read_csv("SCDEE_LOAD_STUDENT_FILE")

    def on_start(self):
        if not self._rows:
            self.environment.runner.quit()
            return
        row = random.choice(self._rows)
        self.email = row["email"]
        self.pw = row["password"]
        self.instance_id = row["instance_id"]
        self.problem_id = row.get("problem_id", "")
        token = _login(self.client, self.email, self.pw)
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


class ManagerPublisher(HttpUser):
    wait_time = between(60, 120)
    weight = 5

    def on_start(self):
        self.email = os.environ.get("SCDEE_LOAD_MANAGER_EMAIL", "")
        self.pw = os.environ.get("SCDEE_LOAD_MANAGER_PW", "")
        self.exam_id = os.environ.get("SCDEE_LOAD_EXAM_ID", "")
        if not (self.email and self.pw and self.exam_id):
            self._auth = False
            return
        token = _login(self.client, self.email, self.pw)
        if not token:
            self._auth = False
            return
        self.client.headers["Authorization"] = f"Bearer {token}"
        self._auth = True

    @task
    def publish(self):
        if self._auth:
            self.client.post(f"/api/v1/exams/{self.exam_id}/publish/", name="manager/publish")
