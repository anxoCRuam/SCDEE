"""
Integration tests for the ``sftp_watcher`` management command.

The watcher is a long-running process in production (RF-9.1). For
tests it is invoked with ``--once`` so the scan/enqueue logic runs
synchronously and the test can assert against the resulting state
deterministically.

What we cover:

- A file dropped in the watch directory is enqueued exactly once and
  archived under ``.processed/`` with a UTC timestamp prefix.
- Files with non-recognised extensions are ignored without errors.
- Hidden files (``.partial`` from in-progress SFTP uploads) and the
  ``.processed`` directory itself are skipped.
- Idempotency: running the watcher again over the same directory
  does not re-enqueue archived files.
- ``INGESTION_KEEP_PROCESSED=False`` deletes processed files instead
  of archiving them.

The recognition pipeline itself is *not* exercised here — that
belongs to the lifecycle E2E tests. We assert that
``ingest_page_task.delay`` is called with the right args and stop
there.

References: RF-9.1.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings

# ── Helpers ────────────────────────────────────────────────────────


def _drop_file(directory: Path, filename: str, content: bytes = b"PNG\x89") -> Path:
    """Drop a file with the given content into ``directory``."""
    path = directory / filename
    path.write_bytes(content)
    return path


def _list_archived(directory: Path) -> list[str]:
    """Return the names of files inside ``.processed/``, excluding hidden ones."""
    processed_dir = directory / ".processed"
    if not processed_dir.exists():
        return []
    return sorted(
        f.name for f in processed_dir.iterdir() if f.is_file() and not f.name.startswith(".")
    )


# ── Tests ──────────────────────────────────────────────────────────


def test_sftp_watcher_enqueues_dropped_file_and_archives_it(tmp_path):
    """A PNG dropped in the watch dir is enqueued once and archived."""
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 200
    _drop_file(tmp_path, "page_001.png", content=payload)

    with override_settings(INGESTION_WATCH_DIR=str(tmp_path)):  # noqa: SIM117
        with patch("apps.ingestion.tasks.ingest_page_task.delay") as mock_delay:
            call_command("sftp_watcher", "--once")

    assert mock_delay.call_count == 1, "ingest_page_task.delay should have been called once."

    # The base64-encoded payload and the original filename should both
    # be passed to the task. We don't inspect the base64 string — that
    # would be fragile. We assert on the filename, which is what
    # operators use to correlate.
    args, _kwargs = mock_delay.call_args
    assert (
        args[1] == "page_001.png"
    ), f"Expected filename argument 'page_001.png', got {args[1]!r}."
    # The first arg is the b64-encoded payload. Sanity-check it is
    # non-empty and decodable.
    import base64

    decoded = base64.b64decode(args[0])
    assert decoded == payload, "Decoded base64 payload does not match the original file."

    # Original file is gone from the watch directory.
    assert not (tmp_path / "page_001.png").exists()

    # File ended up archived under .processed/ with a UTC stamp prefix.
    archived = _list_archived(tmp_path)
    assert len(archived) == 1
    archived_name = archived[0]
    assert archived_name.endswith(
        "page_001.png"
    ), f"Archived filename should preserve the original suffix, got {archived_name!r}."
    # Format: ``YYYYMMDD_HHMMSS_<original>`` — the prefix adds 16 chars
    # (8 for date, 1 underscore, 6 for time, 1 underscore).
    assert len(archived_name) == len("page_001.png") + 16, (
        f"Archive timestamp prefix should add 16 chars, "
        f"got name {archived_name!r} (len {len(archived_name)})."
    )


def test_sftp_watcher_ignores_unrecognised_extensions(tmp_path):
    """Files with extensions not in the allowlist are left alone."""
    _drop_file(tmp_path, "notes.txt", content=b"plain text")
    _drop_file(tmp_path, "archive.zip", content=b"PK\x03\x04")
    _drop_file(tmp_path, "page.png", content=b"\x89PNG")

    with override_settings(INGESTION_WATCH_DIR=str(tmp_path)):  # noqa: SIM117
        with patch("apps.ingestion.tasks.ingest_page_task.delay") as mock_delay:
            call_command("sftp_watcher", "--once")

    # Only the .png was enqueued.
    assert mock_delay.call_count == 1

    # The other two are still there, untouched.
    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / "archive.zip").exists()
    # The .png is gone (claimed and archived).
    assert not (tmp_path / "page.png").exists()


def test_sftp_watcher_skips_hidden_files(tmp_path):
    """Hidden files (e.g. ``.upload-in-progress.png``) must be ignored.

    SFTP clients commonly upload files atomically by writing to a
    hidden temp name and renaming on completion. The watcher must
    not pick up the hidden form.
    """
    _drop_file(tmp_path, ".upload-in-progress.png", content=b"\x89PNG")
    _drop_file(tmp_path, "real.png", content=b"\x89PNG")

    with override_settings(INGESTION_WATCH_DIR=str(tmp_path)):  # noqa: SIM117
        with patch("apps.ingestion.tasks.ingest_page_task.delay") as mock_delay:
            call_command("sftp_watcher", "--once")

    # Only ``real.png`` was enqueued.
    assert mock_delay.call_count == 1
    args, _ = mock_delay.call_args
    assert args[1] == "real.png"

    # The hidden file is still there.
    assert (tmp_path / ".upload-in-progress.png").exists()


def test_sftp_watcher_does_not_reprocess_archived_files(tmp_path):
    """A second pass over the directory does not re-enqueue archived files."""
    _drop_file(tmp_path, "page.png", content=b"\x89PNG")

    with override_settings(INGESTION_WATCH_DIR=str(tmp_path)):  # noqa: SIM117
        with patch("apps.ingestion.tasks.ingest_page_task.delay") as mock_delay:
            call_command("sftp_watcher", "--once")
            call_command("sftp_watcher", "--once")

    # Only one enqueue across both passes.
    assert mock_delay.call_count == 1


def test_sftp_watcher_supports_multiple_directories(tmp_path):
    """Multiple watch directories are all scanned in a single pass."""
    dir_a = tmp_path / "scanner_a"
    dir_b = tmp_path / "scanner_b"
    dir_a.mkdir()
    dir_b.mkdir()
    _drop_file(dir_a, "a.png", content=b"\x89PNG-A")
    _drop_file(dir_b, "b.jpg", content=b"\xff\xd8\xff-B")

    with override_settings(INGESTION_WATCH_DIR=[str(dir_a), str(dir_b)]):  # noqa: SIM117
        with patch("apps.ingestion.tasks.ingest_page_task.delay") as mock_delay:
            call_command("sftp_watcher", "--once")

    # Both were enqueued.
    enqueued_filenames = sorted(call.args[1] for call in mock_delay.call_args_list)
    assert enqueued_filenames == ["a.png", "b.jpg"]

    # Both archived under their own .processed/.
    assert _list_archived(dir_a) == [name for name in _list_archived(dir_a) if "a.png" in name]
    assert _list_archived(dir_b) == [name for name in _list_archived(dir_b) if "b.jpg" in name]
    assert len(_list_archived(dir_a)) == 1
    assert len(_list_archived(dir_b)) == 1


def test_sftp_watcher_deletes_when_keep_processed_disabled(tmp_path):
    """With ``INGESTION_KEEP_PROCESSED=False`` the file is removed, not archived."""
    _drop_file(tmp_path, "ephemeral.png", content=b"\x89PNG")

    with (
        override_settings(
            INGESTION_WATCH_DIR=str(tmp_path),
            INGESTION_KEEP_PROCESSED=False,
        ),
        patch("apps.ingestion.tasks.ingest_page_task.delay") as mock_delay,
    ):
        call_command("sftp_watcher", "--once")

    assert mock_delay.call_count == 1
    # No file in the root of the watch dir.
    assert not (tmp_path / "ephemeral.png").exists()
    # And no archive copy either.
    assert _list_archived(tmp_path) == []


def test_sftp_watcher_handles_missing_watch_dir_gracefully(tmp_path):
    """Misconfigured watch directory does not crash the watcher."""
    nonexistent = tmp_path / "does-not-exist"

    with override_settings(INGESTION_WATCH_DIR=str(nonexistent)):  # noqa: SIM117
        with patch("apps.ingestion.tasks.ingest_page_task.delay") as mock_delay:
            # The makedirs(.processed) call inside handle() actually
            # creates the directory; that is OK — the test asserts that
            # the scan does not enqueue anything and exits cleanly.
            call_command("sftp_watcher", "--once")

    assert mock_delay.call_count == 0


def test_sftp_watcher_with_no_watch_dir_logs_warning_and_exits(tmp_path, capsys):
    """No INGESTION_WATCH_DIR configured → friendly stderr message, no crash."""
    with override_settings(INGESTION_WATCH_DIR=[]):  # noqa: SIM117
        with patch("apps.ingestion.tasks.ingest_page_task.delay") as mock_delay:
            call_command("sftp_watcher", "--once")

    assert mock_delay.call_count == 0
    captured = capsys.readouterr()
    assert "No INGESTION_WATCH_DIR" in captured.err
