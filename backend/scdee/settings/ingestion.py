"""
Ingestion – filesystem watcher / SFTP simulation (RF-9.1).
"""

from ._paths import BASE_DIR, env

INGESTION_WATCH_DIR = env.list(
    "INGESTION_WATCH_DIR",
    default=[str(BASE_DIR / "ingestion_inbox")],
)
MAX_PDF_SIZE_MB = env.int("MAX_PDF_SIZE_MB", default=50)

# Assembly: identification matching tuning.
ASSEMBLY_WINDOW_SECONDS = 180  # already existed; kept here for clarity.
ASSEMBLY_MATCH_THRESHOLD = 0.05  # optimistic: assign unless score is basura.
SCORING_WEIGHTS = {  # winner from the offline grid search.
    "name": 0.20,
    "nia": 0.50,
    "dni": 0.30,
}
