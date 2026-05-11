"""
SFTP watcher management command.

Monitors one or more directories for new scanned pages. When a new
file appears, it is atomically claimed, enqueued as an ingestion
task via Celery, and archived under ``.processed/``.

Uses ``shutil.move`` instead of ``os.rename`` to avoid cross-device
link errors on Docker overlay volumes.

The organization is NOT configured here; it is extracted from the
QR code embedded in each scanned page during the recognition step.

Configuration (settings / env):
    INGESTION_WATCH_DIR: str or list[str] — directory paths to watch.
    SFTP_POLL_INTERVAL_SECONDS: polling interval (default 5).
    INGESTION_KEEP_PROCESSED: if False, processed files are deleted
        instead of moved to ``.processed/`` (default True).

Modes:
    Default — runs an infinite scan/sleep loop until interrupted
    (production behaviour, RF-9.1).
    ``--once`` — performs a single scan pass over every configured
    directory and exits with status 0. Used by tests and by manual
    operators who want to drain the inbox once. Idempotent.

References: RF-9.1
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
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
        parser.add_argument(
            "--once",
            action="store_true",
            help=(
                "Run a single scan pass over every configured directory and "
                "exit. Use this for one-off draining or in tests; the default "
                "behaviour is to loop forever."
            ),
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
        once = options["once"]

        self.keep_processed = getattr(settings, "INGESTION_KEEP_PROCESSED", True)

        mode_label = "single pass" if once else f"poll every {poll_interval}s"
        self.stdout.write(
            f"Starting ingestion watcher (pid {os.getpid()}). "
            f"Monitoring {len(directories)} folder(s); {mode_label}. "
            f"Organization will be resolved from QR code."
        )

        for path in directories:
            os.makedirs(os.path.join(path, _PROCESSED_DIR_NAME), exist_ok=True)

        if once:
            # Single scan over each directory. Done.
            for path in directories:
                self._scan_directory(path)
            return

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
        """Claim the file, enqueue ingestion, then archive it.

        ``shutil.move`` is used instead of ``os.rename`` to avoid
        cross-device link errors on Docker overlay/volume mounts.
        """
        claim_path = os.path.join(
            dirpath,
            _PROCESSED_DIR_NAME,
            f".claim.{os.getpid()}.{uuid.uuid4().hex}.{filename}",
        )

        # Step 1 — claim the file by moving it out of the inbox
        try:
            shutil.move(filepath, claim_path)
        except FileNotFoundError:
            return  # Another watcher claimed it; nothing to do.
        except OSError as exc:
            self.stderr.write(f"Failed to claim {filename}: {exc}")
            return

        # Step 2 — read, encode and enqueue
        try:
            with open(claim_path, "rb") as f:
                file_data = f.read()

            file_data_b64 = base64.b64encode(file_data).decode("ascii")

            from apps.ingestion.tasks import ingest_page_task

            ingest_page_task.delay(file_data_b64, filename)
            self.stdout.write(f"Enqueued: {filename}")

            # Step 3 — archive or delete
            if self.keep_processed:
                stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
                final_path = os.path.join(
                    dirpath,
                    _PROCESSED_DIR_NAME,
                    f"{stamp}_{filename}",
                )
                shutil.move(claim_path, final_path)
            else:
                os.remove(claim_path)

        except Exception as exc:
            self.stderr.write(f"Error processing {filename}: {exc}")
            # Restore the file so the next scan retries
            try:
                shutil.move(claim_path, filepath)
            except OSError:
                self.stderr.write(f"Could not restore {filename} after failure")
