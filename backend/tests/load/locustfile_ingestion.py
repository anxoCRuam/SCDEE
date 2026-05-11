"""
Fase 1 – Ingesta masiva por SFTP (sin HTTP).

Simula la llegada de cientos de páginas escaneadas a través del
directorio vigilado por el SFTP watcher. No hay estudiantes ni
correctores interactuando. El objetivo es saturar el pipeline de
reconocimiento (OCR, ensamblaje) y verificar que Celery maneja la
cola sin cuellos de botella.

Uso:
    source /tmp/loadtest/manager.txt
    export SCDEE_LOAD_SFTP_DIR=/ruta/a/backend/ingestion_inbox
    locust -f locustfile_ingestion.py --host=http://localhost:8000 \
        --headless -u 30 -r 5 -t 10m --csv=/tmp/loadtest_phase1
"""

import os
import time
import uuid
from pathlib import Path

from locust import User, between, events, task

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
    b"\x89\x00\x00\x00\rIDATx\x9cc````\x00\x00\x00\x05\x00\x01]\xcc"
    b"\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)


class IngestionUser(User):
    wait_time = between(0.5, 2)  # ritmo rápido de escáner
    weight = 100
    _dir = os.environ.get("SCDEE_LOAD_SFTP_DIR", "")

    def on_start(self):
        self._enabled = bool(self._dir) and Path(self._dir).is_dir()

    @task
    def drop_file(self):
        if not self._enabled:
            return
        fname = f"ingest_{uuid.uuid4().hex[:12]}.png"
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
