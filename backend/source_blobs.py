"""Durable copies of uploaded data sources, kept in the app database.

Uploaded spreadsheets and Tally reports become a SQLite file on local disk.
On hosts with an ephemeral disk (Render's free plan) that file disappears on
every restart, while the source row in Postgres survives. This module keeps a
zlib-compressed copy of the file in ``workspace_source_blobs`` and writes it
back to disk when it is missing.

File contents are never logged. A copy can only be restored for the same
workspace id *and* tenant id it was saved for.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import tempfile
import threading
import zlib
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.models import WorkspaceRecord, WorkspaceSourceBlob

logger = logging.getLogger(__name__)

# Compressed bytes stored per source. The upload parser already caps input at
# 25 MiB, so a normal source stays far below this; the cap protects the
# database from an unusually large generated file.
DEFAULT_MAX_STORED_BYTES = 50 * 1024 * 1024
MAX_STORED_BYTES_ENV = "PUCHOO_SOURCE_BLOB_MAX_BYTES"
_SAFE_FILE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}\.(db|sqlite|sqlite3)$")
_restore_lock = threading.Lock()


def max_stored_bytes() -> int:
    raw = os.getenv(MAX_STORED_BYTES_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_MAX_STORED_BYTES


def _workspace_root() -> Path:
    # Read the module attribute each time so tests (and env overrides) apply.
    from apps.core import workspaces

    return Path(workspaces.LOCAL_WORKSPACE_DIRECTORY).expanduser().resolve()


def _metadata_path(database_file: Path) -> Path:
    from apps.core.data_privacy import report_metadata_path

    return report_metadata_path(database_file)


def managed_source_path(source_ref: str | None, workspace_id: str) -> Path:
    """Where a source's file lives (or is restored to) inside the upload directory.

    A stored path that points outside the upload directory is never written
    to; the file is restored under the directory with the same file name, or
    a name derived from the workspace id.
    """

    root = _workspace_root()
    if source_ref:
        candidate = Path(source_ref).expanduser()
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            resolved = None
        if resolved is not None and resolved != root and resolved.is_relative_to(root):
            return resolved
        if _SAFE_FILE_NAME.match(candidate.name):
            return root / candidate.name
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", workspace_id)[:80] or "source"
    return root / f"pucho_restored_{safe_id}.db"


def _snapshot(database_file: Path) -> bytes:
    """Read a consistent copy of a SQLite file, including WAL content."""

    with tempfile.TemporaryDirectory(prefix="pucho_snapshot_") as scratch:
        copy_path = Path(scratch) / "copy.db"
        with closing(sqlite3.connect(f"{database_file.resolve().as_uri()}?mode=ro", uri=True)) as source, \
                closing(sqlite3.connect(copy_path)) as target:
            source.backup(target)
        return copy_path.read_bytes()


@dataclass(frozen=True)
class StoreResult:
    stored: bool
    reason: str = ""


def store_source(db: Session, *, workspace_id: str, tenant_id: str, database_file: Path) -> StoreResult:
    """Save (or replace) the durable copy of one source. Caller commits."""

    database_file = Path(database_file)
    if not database_file.is_file():
        return StoreResult(False, "missing")
    try:
        raw = _snapshot(database_file)
    except (sqlite3.Error, OSError):
        logger.warning("source_blob_snapshot_failed workspace=%s", workspace_id)
        return StoreResult(False, "unreadable")
    compressed = zlib.compress(raw, 6)
    metadata_file = _metadata_path(database_file)
    metadata = zlib.compress(metadata_file.read_bytes(), 6) if metadata_file.is_file() else None
    if len(compressed) + len(metadata or b"") > max_stored_bytes():
        # The source still works until the next restart; the UI then offers a re-upload.
        logger.warning("source_blob_too_large workspace=%s bytes=%s", workspace_id, len(compressed))
        db.execute(delete(WorkspaceSourceBlob).where(WorkspaceSourceBlob.workspace_id == workspace_id))
        return StoreResult(False, "too_large")
    blob = db.get(WorkspaceSourceBlob, workspace_id)
    if blob is not None and blob.tenant_id != tenant_id:
        raise ValueError("Source copy belongs to another tenant")
    if blob is None:
        blob = WorkspaceSourceBlob(workspace_id=workspace_id, tenant_id=tenant_id)
        db.add(blob)
    blob.content = compressed
    blob.report_metadata = metadata
    blob.size_bytes = len(raw)
    blob.sha256 = hashlib.sha256(raw).hexdigest()
    db.flush()
    logger.info("source_blob_stored workspace=%s bytes=%s compressed=%s", workspace_id, len(raw), len(compressed))
    return StoreResult(True)


def has_source(db: Session, *, workspace_id: str, tenant_id: str) -> bool:
    return db.scalar(select(WorkspaceSourceBlob.workspace_id).where(
        WorkspaceSourceBlob.workspace_id == workspace_id,
        WorkspaceSourceBlob.tenant_id == tenant_id,
    )) is not None


def delete_source(db: Session, workspace_id: str) -> None:
    db.execute(delete(WorkspaceSourceBlob).where(WorkspaceSourceBlob.workspace_id == workspace_id))


def delete_tenant_sources(db: Session, tenant_id: str) -> None:
    db.execute(delete(WorkspaceSourceBlob).where(WorkspaceSourceBlob.tenant_id == tenant_id))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".restore_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def restore_source(db: Session, record: WorkspaceRecord) -> Path | None:
    """Write a missing source file back to disk from its durable copy.

    Returns the restored path, or ``None`` when there is no usable copy. When
    the file has to live at a new path, ``record.source_ref`` is updated and
    the caller commits. Safe to call from concurrent requests: every write
    goes to a private temp file that is atomically renamed into place.
    """

    if record.source_type == "server":
        return None
    blob = db.scalar(select(WorkspaceSourceBlob).where(
        WorkspaceSourceBlob.workspace_id == record.id,
        WorkspaceSourceBlob.tenant_id == record.tenant_id,
    ))
    if blob is None:
        return None
    target = managed_source_path(record.source_ref, record.id)
    with _restore_lock:
        if not target.is_file():
            try:
                raw = zlib.decompress(blob.content)
            except zlib.error:
                logger.warning("source_blob_corrupt workspace=%s", record.id)
                return None
            if hashlib.sha256(raw).hexdigest() != blob.sha256:
                logger.warning("source_blob_checksum_mismatch workspace=%s", record.id)
                return None
            try:
                if blob.report_metadata:
                    _atomic_write(_metadata_path(target), zlib.decompress(blob.report_metadata))
                _atomic_write(target, raw)
            except (OSError, zlib.error):
                logger.warning("source_blob_restore_failed workspace=%s", record.id)
                return None
            logger.info("source_blob_restored workspace=%s bytes=%s", record.id, len(raw))
    if record.source_ref != str(target):
        record.source_ref = str(target)
    return target
