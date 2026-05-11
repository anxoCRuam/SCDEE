"""
Fase 2 – Corrección y anotación (steady state).

Simula el trabajo de los correctores una vez que todas las instancias
están ensambladas y listas para ser calificadas. El foco está en
las anotaciones (texto), que representan la mayor parte del tiempo
del corrector. Las lecturas de instancia completan el perfil.

El grading se ha omitido porque su control de concurrencia requiere
old_grade, aún no implementado en esta versión de la API. Cuando se
implemente, se podrá añadir una tarea de grading con el campo
adecuado.

Uso:
    source /tmp/loadtest/manager.txt
    locust -f locustfile_correction.py --host=http://localhost:8000 \
        --headless -u 20 -r 2 -t 10m --csv=/tmp/loadtest_phase2
"""

import csv
import os
import random
from pathlib import Path

from locust import HttpUser, between, task

# ── Helpers ──────────────────────────────────────────────────


def _login(client, email, pw):
    r = client.post(
        "/api/v1/auth/login/",
        json={"email": email, "password": pw},
        name="auth/login",
    )
    return r.json()["access_token"] if r.status_code == 200 else None


def _read_csv(env_var):
    path = os.environ.get(env_var)
    if not path or not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Frases típicas de anotación ──────────────────────────────

_ANNOTATION_TEXTS = [
    "Revisar el desarrollo del paso 3.",
    "Falta justificar el cambio de variable.",
    "El resultado es correcto pero la notación es confusa.",
    "Aquí hay un error de signo.",
    "Bien razonado.",
    "No se entiende la conclusión.",
    "Unidades incorrectas.",
    "Falta la constante de integración.",
    "La derivada está mal calculada.",
    "Completar el diagrama de flujo.",
]


# ═══════════════════════════════════════════════════════════════
# Grader user
# ═══════════════════════════════════════════════════════════════


class GraderUser(HttpUser):
    wait_time = between(1, 3)
    weight = 100
    _rows = _read_csv("SCDEE_LOAD_GRADER_FILE")

    def on_start(self):
        if not self._rows:
            self.environment.runner.quit()
            return
        row = random.choice(self._rows)
        self.email = row["email"]
        self.pw = row["password"]
        self.instance_id = row["instance_id"]
        self.problem_id = row["problem_id"]
        token = _login(self.client, self.email, self.pw)
        if not token:
            self._auth = False
            return
        self.client.headers["Authorization"] = f"Bearer {token}"
        self._auth = True

    @task(1)
    def my_tasks(self):
        """Listar tareas pendientes (poco frecuente)."""
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
        """Crear una anotación de texto (acción principal)."""
        if not self._auth:
            return
        payload = random.choice(_ANNOTATION_TEXTS)
        self.client.post(
            f"/api/v1/instances/{self.instance_id}/annotations/",
            json={
                "annotation_type": "TEXT",
                "payload": payload,
                "page_number": random.randint(1, 3),
                "x": round(random.uniform(10, 90), 1),
                "y": round(random.uniform(10, 90), 1),
            },
            name="grader/annotations/create",
        )
