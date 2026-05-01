"""
Ingestion – filesystem watcher / SFTP simulation (RF-9.1).
"""

from ._paths import BASE_DIR, env

INGESTION_WATCH_DIR = env.list(
    "INGESTION_WATCH_DIR",
    default=[str(BASE_DIR / "ingestion_inbox")],
)
MAX_PDF_SIZE_MB = env.int("MAX_PDF_SIZE_MB", default=50)
