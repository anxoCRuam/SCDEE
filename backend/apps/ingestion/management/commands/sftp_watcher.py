"""
SFTP watcher management command.

Monitors one or more directories for new scanned pages. When a new
file appears, it is atomically claimed (os.rename), enqueued as an
ingestion task via Celery, and archived under ``.processed/``.

Idempotency: claiming the file with ``os.rename`` is atomic on POSIX,
so if two watcher processes race for the same file only one wins —
the loser sees ``FileNotFoundError`` and silently skips. Files in the
``.processed/`` archive survive watcher restarts and are ignored on
subsequent scans.

The organization is NOT configured here; it is extracted from the
QR code embedded in each scanned page during the recognition step.

Configuration (settings / env):
    INGESTION_WATCH_DIR: str or list[str] — directory paths to watch.
    SFTP_POLL_INTERVAL_SECONDS: polling interval (default 5).
    INGESTION_KEEP_PROCESSED: if False, processed files are deleted
        instead of moved to ``.processed/`` (default True).

References: RF-9.1
"""

from __future__ import annotations

import base64
import logging
import os
import time
import uuid
from datetime import UTC, datetime

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

_PROCESSED_DIR_NAME = ".processed"
_VALID_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".pdf")


class Command(BaseCommand):
    help = "Monitor ingestion directories for new scanned pages and enqueue ingestion."

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-interval",
            type=int,
            default=None,
            help="Polling interval in seconds (overrides settings).",
        )

    def handle(self, *args, **options):
        watch = getattr(settings, "INGESTION_WATCH_DIR", [])
        directories: list[str] = [watch] if isinstance(watch, str) else list(watch)

        if not directories:
            self.stderr.write(
                "No INGESTION_WATCH_DIR configured. "
                "Set it to a directory path (or list of paths) in settings / .env."
            )
            return

        poll_interval = options["poll_interval"] or getattr(
            settings, "SFTP_POLL_INTERVAL_SECONDS", 5
        )

        self.keep_processed = getattr(settings, "INGESTION_KEEP_PROCESSED", True)

        self.stdout.write(
            f"Starting ingestion watcher (pid {os.getpid()}). "
            f"Monitoring {len(directories)} folder(s), polling every {poll_interval}s. "
            f"Organization will be resolved from QR code."
        )

        for path in directories:
            os.makedirs(os.path.join(path, _PROCESSED_DIR_NAME), exist_ok=True)

        try:
            while True:
                for path in directories:
                    self._scan_directory(path)
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            self.stdout.write("Ingestion watcher stopped.")

    def _scan_directory(self, path: str) -> None:
        if not os.path.isdir(path):
            return

        for filename in os.listdir(path):
            if filename == _PROCESSED_DIR_NAME or filename.startswith("."):
                continue

            filepath = os.path.join(path, filename)
            if not os.path.isfile(filepath):
                continue

            if os.path.splitext(filename)[1].lower() not in _VALID_EXTENSIONS:
                continue

            self._claim_and_enqueue(path, filepath, filename)

    def _claim_and_enqueue(self, dirpath: str, filepath: str, filename: str) -> None:
        """Atomically claim the file, enqueue ingestion, then archive it.

        Uses ``os.rename`` to take ownership: only one watcher succeeds
        for a given file; competing watchers get FileNotFoundError and
        skip silently. The renamed claim path is unique per attempt
        (pid + uuid) so no two watchers can produce the same target.
        """
        claim_path = os.path.join(
            dirpath,
            _PROCESSED_DIR_NAME,
            f".claim.{os.getpid()}.{uuid.uuid4().hex}.{filename}",
        )

        try:
            os.rename(filepath, claim_path)
        except FileNotFoundError:
            return  # Another watcher claimed it; nothing to do.
        except OSError as exc:
            logger.exception("Failed to claim %s: %s", filepath, exc)
            return

        try:
            with open(claim_path, "rb") as f:
                file_data = f.read()

            file_data_b64 = base64.b64encode(file_data).decode("ascii")

            from apps.ingestion.tasks import ingest_page_task

            ingest_page_task.delay(file_data_b64, filename)
            self.stdout.write(f"Enqueued: {filename}")

            if self.keep_processed:
                stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
                final_path = os.path.join(
                    dirpath,
                    _PROCESSED_DIR_NAME,
                    f"{stamp}_{filename}",
                )
                os.rename(claim_path, final_path)
            else:
                os.remove(claim_path)

        except Exception as exc:
            # Restore the file if anything went wrong so the next scan retries.
            logger.exception("Error processing %s", filename)
            self.stderr.write(f"Error processing {filename}: {exc}")
            try:
                os.rename(claim_path, filepath)
            except OSError:
                logger.exception("Could not restore %s after failure", filename)
