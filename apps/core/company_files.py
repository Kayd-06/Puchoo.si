"""A company (data source) that holds many files, one SQLite table per file.

Every uploaded data source is one SQLite database. A company keeps adding
tables to that same database: one per Tally report type or spreadsheet file.
Re-uploading the same Tally report appends to its table with the existing
voucher dedupe, so a company never double counts.

What each table came from (label, report, period, dates) lives in the local
sidecar next to the database (``<db>.report.json``), never inside a table, a
schema string or a model prompt. The sidecar travels with the durable copy in
``workspace_source_blobs``, so it survives a wiped disk.

Every change is made on a private copy of the database and swapped in with
``os.replace``. If anything fails, the original file and sidecar are left as
they were.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone, UTC
from pathlib import Path
from typing import Any
from collections.abc import Callable, Iterator

import pandas as pd

from apps.core import tally_import
from apps.core.data_privacy import report_metadata_path

COMPANY_KIND = "company"
FILE_KIND_TALLY = "tally"
FILE_KIND_TABLE = "table"
MAX_TABLE_NAME = 63
MAX_LABEL = 120
APPEND = "append"
NEW = "new"
# Batch uploads: same report appends, anything else becomes a new table.
AUTO = "auto"
LEDGER_TABLE = "ledger_vouchers"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class CompanyFileError(ValueError):
    """A user-safe message about a company file change."""


class CompanyMismatchError(CompanyFileError):
    """The report's Tally header names a different company than the saved data."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _lock_for(path: Path) -> threading.Lock:
    key = str(Path(path).resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------

def safe_table_name(value: object, fallback: str = "data") -> str:
    """Lower-case SQL identifier made of letters, digits and underscores."""

    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).strip("_").lower()
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"t_{normalized}"
    if normalized.startswith("sqlite_"):
        normalized = f"t_{normalized}"
    return normalized[:MAX_TABLE_NAME]


def unique_table_name(value: object, existing: set[str] | list[str], fallback: str = "data") -> str:
    """A safe name that does not clash (SQLite names ignore case) with ``existing``."""

    taken = {name.lower() for name in existing}
    base = safe_table_name(value, fallback)
    candidate, index = base, 2
    while candidate.lower() in taken:
        suffix = f"_{index}"
        candidate = f"{base[: MAX_TABLE_NAME - len(suffix)]}{suffix}"
        index += 1
    return candidate


def clean_label(value: object, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return (text or fallback)[:MAX_LABEL]


def normalize_company_name(value: object) -> str:
    """Compare Tally company names loosely: case, spacing and a trailing FY tag."""

    text = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    text = re.sub(r"\s*\(\s*(?:fy\s*)?\d{2,4}\s*[-/]\s*\d{2,4}\s*\)\s*$", "", text)
    return text.rstrip(" .,")


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


# --------------------------------------------------------------------------
# Sidecar manifest
# --------------------------------------------------------------------------

def read_manifest(database_file: Path) -> dict[str, Any]:
    path = report_metadata_path(Path(database_file))
    payload = tally_import.read_metadata(path) if path.is_file() else {}
    reports = payload.get("reports") if isinstance(payload.get("reports"), list) else []
    files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    return {
        "kind": payload.get("kind") or COMPANY_KIND,
        "reports": [item for item in reports if isinstance(item, dict)],
        "files": {str(key): value for key, value in files.items() if isinstance(value, dict)},
    }


def _manifest_kind(manifest: dict[str, Any]) -> str:
    has_tally = bool(manifest.get("reports")) or any(
        entry.get("kind") == FILE_KIND_TALLY for entry in manifest.get("files", {}).values()
    )
    return FILE_KIND_TALLY if has_tally else COMPANY_KIND


def _encode_manifest(manifest: dict[str, Any]) -> bytes:
    payload = {
        "kind": _manifest_kind(manifest),
        "reports": manifest.get("reports", []),
        "files": manifest.get("files", {}),
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def user_tables(database_file: Path) -> list[str]:
    with closing(sqlite3.connect(f"file:{Path(database_file).resolve()}?mode=ro", uri=True)) as connection:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        ]


def count_tables(database_file: Path | None) -> int | None:
    if database_file is None or not Path(database_file).is_file():
        return None
    try:
        return len(user_tables(database_file))
    except sqlite3.Error:
        return None


def known_companies(manifest: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in manifest.get("reports", []):
        name = item.get("company_name")
        if name and str(name) not in names:
            names.append(str(name))
    return names


def is_different_company(manifest: dict[str, Any], company_name: str | None) -> bool:
    if not company_name:
        return False
    known = {normalize_company_name(name) for name in known_companies(manifest)}
    known.discard("")
    return bool(known) and normalize_company_name(company_name) not in known


def table_report_types(database_file: Path | None) -> dict[str, str]:
    """Table -> Tally report type, for schema notes on renamed report tables."""

    if database_file is None:
        return {}
    manifest = read_manifest(database_file)
    types: dict[str, str] = {}
    for item in manifest["reports"]:
        if item.get("table") and item.get("report_type"):
            types[str(item["table"])] = str(item["report_type"])
    for table, entry in manifest["files"].items():
        if entry.get("kind") == FILE_KIND_TALLY and entry.get("report_type"):
            types[table] = str(entry["report_type"])
    return types


def _file_entry(manifest: dict[str, Any], table: str) -> dict[str, Any]:
    return manifest["files"].get(table) or {}


def _reports_for(manifest: dict[str, Any], table: str) -> list[dict[str, Any]]:
    return [item for item in manifest["reports"] if item.get("table") == table]


def list_files(database_file: Path, *, fallback_added_at: str | None = None) -> list[dict[str, Any]]:
    """Every table in the company with its row count, period and dates."""

    database_file = Path(database_file)
    manifest = read_manifest(database_file)
    files: list[dict[str, Any]] = []
    with closing(sqlite3.connect(f"file:{database_file.resolve()}?mode=ro", uri=True)) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        ]
        for table in tables:
            entry = _file_entry(manifest, table)
            reports = _reports_for(manifest, table)
            rows = int(connection.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
            columns = len(connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall())
            starts = sorted(str(item["period_start"]) for item in reports if item.get("period_start"))
            ends = sorted(str(item["period_end"]) for item in reports if item.get("period_end"))
            dates = sorted(str(item["report_date"]) for item in reports if item.get("report_date"))
            imported = sorted(str(item["imported_at"]) for item in reports if item.get("imported_at"))
            kind = entry.get("kind") or (FILE_KIND_TALLY if reports else FILE_KIND_TABLE)
            report_type = entry.get("report_type") or (reports[-1].get("report_type") if reports else None)
            files.append({
                "table": table,
                "label": entry.get("label") or (reports[-1].get("report_name") if reports else table),
                "kind": kind,
                "report_type": report_type,
                "report_name": entry.get("report_name") or (reports[-1].get("report_name") if reports else None),
                "rows": rows,
                "columns": columns,
                "period_start": starts[0] if starts else None,
                "period_end": ends[-1] if ends else None,
                "report_date": dates[-1] if dates else None,
                "uploads": len(reports) if reports else (1 if entry else None),
                "added_at": entry.get("added_at") or (imported[0] if imported else fallback_added_at),
                "updated_at": entry.get("updated_at") or (imported[-1] if imported else None),
            })
    return files


# --------------------------------------------------------------------------
# Safe changes: private copy, then atomic swap
# --------------------------------------------------------------------------

def _copy_database(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)) as origin, \
            closing(sqlite3.connect(target)) as copy:
        origin.backup(copy)


def _fsync_write(path: Path, data: bytes) -> None:
    with open(path, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _working_copy(database_file: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield a private copy and manifest; swap both in only if the block succeeds."""

    database_file = Path(database_file)
    if not database_file.is_file():
        raise CompanyFileError("This data source's file is no longer available. Re-upload it first.")
    metadata_file = report_metadata_path(database_file)
    with _lock_for(database_file):
        scratch = Path(tempfile.mkdtemp(prefix=".pucho_change_", dir=database_file.parent))
        try:
            working = scratch / "working.db"
            _copy_database(database_file, working)
            manifest = read_manifest(database_file)
            yield working, manifest
            with closing(sqlite3.connect(working)) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise CompanyFileError("The updated data could not be checked, so nothing was changed.")
                tables = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()[0]
            if not tables:
                raise CompanyFileError("A data source needs at least one table.")
            new_metadata = scratch / "working.report.json"
            _fsync_write(new_metadata, _encode_manifest(manifest))
            backup_db = scratch / "previous.db"
            backup_metadata = scratch / "previous.report.json"
            shutil.copy2(database_file, backup_db)
            had_metadata = metadata_file.is_file()
            if had_metadata:
                shutil.copy2(metadata_file, backup_metadata)
            try:
                os.replace(working, database_file)
                os.replace(new_metadata, metadata_file)
            except BaseException:
                os.replace(backup_db, database_file)
                if had_metadata:
                    os.replace(backup_metadata, metadata_file)
                else:
                    metadata_file.unlink(missing_ok=True)
                raise
            for suffix in ("-wal", "-shm", "-journal"):
                database_file.with_name(database_file.name + suffix).unlink(missing_ok=True)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


@contextmanager
def snapshot_for_rollback(database_file: Path) -> Iterator[Callable[[], None]]:
    """Keep a copy of the file and sidecar so a caller can undo a swap that followed."""

    database_file = Path(database_file)
    metadata_file = report_metadata_path(database_file)
    scratch = Path(tempfile.mkdtemp(prefix=".pucho_undo_", dir=database_file.parent))
    try:
        saved_db = scratch / "saved.db"
        saved_metadata = scratch / "saved.report.json"
        shutil.copy2(database_file, saved_db)
        had_metadata = metadata_file.is_file()
        if had_metadata:
            shutil.copy2(metadata_file, saved_metadata)

        def restore() -> None:
            with _lock_for(database_file):
                shutil.copy2(saved_db, scratch / "restore.db")
                os.replace(scratch / "restore.db", database_file)
                if had_metadata:
                    shutil.copy2(saved_metadata, scratch / "restore.report.json")
                    os.replace(scratch / "restore.report.json", metadata_file)
                else:
                    metadata_file.unlink(missing_ok=True)

        yield restore
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def new_company_file(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="pucho_company_", suffix=".db", dir=destination, delete=False) as handle:
        return Path(handle.name)


def discard_company_file(path: Path) -> None:
    for candidate in (path, report_metadata_path(path)):
        candidate.unlink(missing_ok=True)


def write_manifest_direct(database_file: Path, manifest: dict[str, Any]) -> None:
    """Write the sidecar of a brand-new file that is not registered yet."""

    target = report_metadata_path(Path(database_file))
    temporary = target.with_name(target.name + ".tmp")
    _fsync_write(temporary, _encode_manifest(manifest))
    os.replace(temporary, target)


# --------------------------------------------------------------------------
# Adding files
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TallyPlan:
    mode: str
    table: str


def _existing_tally_tables(
    manifest: dict[str, Any], tables: list[str], report_type: str, report_name: str | None = None
) -> list[str]:
    """Tables that already hold this kind of Tally report (same type and title)."""

    wanted = re.sub(r"\s+", " ", str(report_name or "")).strip().casefold()
    types: dict[str, str] = {}
    names: dict[str, set[str]] = {}
    for item in manifest["reports"]:
        if item.get("table") and item.get("report_type"):
            types[str(item["table"])] = str(item["report_type"])
            names.setdefault(str(item["table"]), set()).add(
                re.sub(r"\s+", " ", str(item.get("report_name") or "")).strip().casefold())
    for table, entry in manifest["files"].items():
        if entry.get("kind") == FILE_KIND_TALLY and entry.get("report_type"):
            types[table] = str(entry["report_type"])
            names.setdefault(table, set()).add(
                re.sub(r"\s+", " ", str(entry.get("report_name") or "")).strip().casefold())
    present = {name.lower(): name for name in tables}
    matches: list[str] = []
    for table in tables:
        key = next((name for name in types if name.lower() == table.lower()), None)
        if key is None or types[key] != report_type:
            continue
        if wanted and wanted not in names.get(key, set()):
            continue
        matches.append(present[table.lower()])
    return matches


def tally_choices(database_file: Path | None, report: tally_import.TallyReport) -> dict[str, Any]:
    """What adding this report to a company would do, for the preview."""

    if database_file is None:
        return {
            "tables": [],
            "append_candidates": [],
            "suggested_mode": NEW,
            "suggested_table": report.table,
            "different_company": False,
            "company_on_file": None,
        }
    tables = user_tables(database_file)
    manifest = read_manifest(database_file)
    candidates = _existing_tally_tables(manifest, tables, report.report_type, report.metadata.report_name)
    # A second ledger is a different account, not a new month of the same one,
    # so ledgers start as their own table unless the user picks one.
    same_name = next((name for name in candidates if name.lower() == report.table.lower()), None)
    append_default = same_name is not None and report.table != LEDGER_TABLE
    suggested_table = same_name if append_default else unique_table_name(report.table, tables)
    overlap = 0
    if append_default and report.report_type == tally_import.VOUCHER_REGISTER:
        overlap = tally_import.existing_voucher_overlap(replace(report, table=same_name), database_file)
    known = known_companies(manifest)
    return {
        "tables": tables,
        "append_candidates": candidates,
        "suggested_mode": APPEND if append_default else NEW,
        "suggested_table": suggested_table,
        "different_company": is_different_company(manifest, report.metadata.company_name),
        "company_on_file": known[0] if known else None,
        "existing_vouchers": overlap,
        "new_rows": max(0, len(report.rows) - overlap),
    }


def append_overlap(database_file: Path, report: tally_import.TallyReport, table: str) -> dict[str, int]:
    duplicates = 0
    if report.report_type == tally_import.VOUCHER_REGISTER:
        duplicates = tally_import.existing_voucher_overlap(replace(report, table=table), database_file)
    return {"existing_vouchers": duplicates, "new_rows": max(0, len(report.rows) - duplicates)}


def resolve_tally_plan(
    tables: list[str], manifest: dict[str, Any], report: tally_import.TallyReport, *, mode: str, table_name: str
) -> TallyPlan:
    candidates = _existing_tally_tables(manifest, tables, report.report_type, report.metadata.report_name)
    by_lower = {name.lower(): name for name in tables}
    requested = table_name.strip()
    if mode == APPEND:
        target = by_lower.get(safe_table_name(requested or report.table).lower())
        if target is None or target not in candidates:
            raise CompanyFileError("Pick a table with the same kind of Tally report to add these rows to.")
        return TallyPlan(APPEND, target)
    if mode == NEW:
        name = safe_table_name(requested or report.table)
        if name.lower() in by_lower:
            raise CompanyFileError(
                f"This company already has a table named {name}. Pick another name, or add the rows to that table."
            )
        return TallyPlan(NEW, name)
    if mode == AUTO:
        # Several files saved in one go: never ask, never overwrite. The same
        # report (a new period) is appended with voucher dedupe; ledgers and
        # different reports get their own table.
        if requested:
            name = safe_table_name(requested)
            existing = by_lower.get(name.lower())
            if existing is None:
                return TallyPlan(NEW, name)
            if existing in candidates and report.table != LEDGER_TABLE:
                return TallyPlan(APPEND, existing)
            raise CompanyFileError(
                f"This company already has a table named {name}. Pick another name for this file."
            )
        same = by_lower.get(report.table.lower())
        if same is not None and same in candidates and report.table != LEDGER_TABLE:
            return TallyPlan(APPEND, same)
        return TallyPlan(NEW, unique_table_name(report.table, tables))
    # Older clients send no mode: keep the old append behaviour for the same report.
    same = by_lower.get(report.table.lower())
    if same is not None and same in candidates:
        return TallyPlan(APPEND, same)
    return TallyPlan(NEW, unique_table_name(report.table, tables))


def _record_tally(
    manifest: dict[str, Any],
    report: tally_import.TallyReport,
    result: tally_import.SaveResult,
    *,
    table: str,
    label: str | None,
    source_file: str | None,
    replace_existing: bool = False,
) -> None:
    now = _now()
    if replace_existing:
        manifest["reports"] = [item for item in manifest["reports"] if item.get("table") != table]
    manifest["reports"].append({
        **{key: getattr(report.metadata, key) for key in (
            "company_name", "report_name", "period_start", "period_end", "report_date")},
        "report_type": report.report_type,
        "table": table,
        "rows_added": result.rows_added,
        "rows_skipped": result.rows_skipped,
        "rows_replaced": result.rows_replaced,
        "totals_status": report.totals_status,
        "imported_at": now,
    })
    entry = dict(manifest["files"].get(table) or {})
    if replace_existing or not entry:
        entry["added_at"] = entry.get("added_at") or now
    entry.update(
        kind=FILE_KIND_TALLY,
        report_type=report.report_type,
        report_name=report.metadata.report_name,
        label=clean_label(label or entry.get("label"), report.metadata.report_name),
        updated_at=now,
    )
    if source_file:
        entry["source_file"] = clean_label(source_file, "file")
    entry.setdefault("added_at", now)
    manifest["files"][table] = entry


def _save_tally(database_file: Path, report: tally_import.TallyReport, table: str) -> tally_import.SaveResult:
    return tally_import.save_report(replace(report, table=table), database_file)


def save_first_tally(
    database_file: Path,
    report: tally_import.TallyReport,
    *,
    table_name: str = "",
    label: str | None = None,
    source_file: str | None = None,
) -> tuple[tally_import.SaveResult, str]:
    """Write the first report into a brand-new company file."""

    table = safe_table_name(table_name or report.table)
    result = _save_tally(database_file, report, table)
    manifest = read_manifest(database_file)
    _record_tally(manifest, report, result, table=table, label=label, source_file=source_file)
    write_manifest_direct(database_file, manifest)
    return replace(result, table=table), table


def add_tally_report(
    database_file: Path,
    report: tally_import.TallyReport,
    *,
    mode: str = "",
    table_name: str = "",
    label: str | None = None,
    source_file: str | None = None,
    allow_other_company: bool = False,
) -> tuple[tally_import.SaveResult, TallyPlan]:
    with _working_copy(database_file) as (working, manifest):
        if not allow_other_company and is_different_company(manifest, report.metadata.company_name):
            raise CompanyMismatchError(
                "This report is from a different company than the data already saved here. "
                "Confirm that you want to add it anyway."
            )
        plan = resolve_tally_plan(user_tables(working), manifest, report, mode=mode, table_name=table_name)
        result = _save_tally(working, report, plan.table)
        _record_tally(manifest, report, result, table=plan.table, label=label, source_file=source_file)
    return replace(result, table=plan.table), plan


def _write_frame(connection: sqlite3.Connection, table: str, frame: pd.DataFrame) -> int:
    frame.to_sql(table, connection, if_exists="fail", index=False)
    return len(frame.index)


def _record_table(manifest: dict[str, Any], table: str, *, label: str | None, source_file: str | None,
                  replace_existing: bool = False) -> None:
    now = _now()
    entry = dict(manifest["files"].get(table) or {}) if replace_existing else {}
    manifest["reports"] = [item for item in manifest["reports"] if item.get("table") != table]
    entry.update(kind=FILE_KIND_TABLE, label=clean_label(label or entry.get("label"), table), updated_at=now)
    entry.pop("report_type", None)
    entry.pop("report_name", None)
    entry.setdefault("added_at", now)
    if source_file:
        entry["source_file"] = clean_label(source_file, "file")
    manifest["files"][table] = entry


def _frame_names(frames: dict[str, pd.DataFrame], base_name: str, existing: list[str]) -> list[tuple[str, pd.DataFrame]]:
    usable = [(name, frame) for name, frame in frames.items() if not (frame.empty and len(frame.columns) == 0)]
    taken = list(existing)
    planned: list[tuple[str, pd.DataFrame]] = []
    for sheet, frame in usable:
        wanted = base_name if len(usable) == 1 else f"{base_name}_{sheet}"
        table = unique_table_name(wanted, taken)
        taken.append(table)
        planned.append((table, frame))
    return planned


def save_first_frames(
    database_file: Path,
    frames: dict[str, pd.DataFrame],
    *,
    base_name: str,
    label: str | None = None,
    source_file: str | None = None,
) -> list[str]:
    planned = _frame_names(frames, safe_table_name(base_name, "sheet"), [])
    if not planned:
        raise CompanyFileError("The uploaded spreadsheet has no queryable columns.")
    with closing(sqlite3.connect(database_file)) as connection:
        for table, frame in planned:
            _write_frame(connection, table, frame)
        connection.commit()
    manifest = read_manifest(database_file)
    for table, _ in planned:
        _record_table(manifest, table, label=label if len(planned) == 1 else f"{label or base_name} ({table})",
                      source_file=source_file)
    write_manifest_direct(database_file, manifest)
    return [table for table, _ in planned]


def add_frames(
    database_file: Path,
    frames: dict[str, pd.DataFrame],
    *,
    base_name: str,
    label: str | None = None,
    source_file: str | None = None,
) -> list[str]:
    """Add a plain spreadsheet (one table per sheet) to an existing company."""

    return add_frame_groups(database_file, [(frames, base_name, label, source_file)])


def add_frame_groups(
    database_file: Path,
    groups: list[tuple[dict[str, pd.DataFrame], str, str | None, str | None]],
) -> list[str]:
    """Add several plain files in one change: all of them are saved, or none."""

    added: list[str] = []
    with _working_copy(database_file) as (working, manifest):
        taken = user_tables(working)
        with closing(sqlite3.connect(working)) as connection:
            for frames, base_name, label, source_file in groups:
                planned = _frame_names(frames, safe_table_name(base_name, "sheet"), taken)
                if not planned:
                    raise CompanyFileError("The uploaded spreadsheet has no queryable columns.")
                for table, frame in planned:
                    _write_frame(connection, table, frame)
                    taken.append(table)
                    added.append(table)
                    _record_table(
                        manifest, table,
                        label=label if len(planned) == 1 else f"{label or base_name} ({table})",
                        source_file=source_file,
                    )
            connection.commit()
    return added


# --------------------------------------------------------------------------
# Removing and replacing one file
# --------------------------------------------------------------------------

def _require_table(tables: list[str], table: str) -> str:
    match = next((name for name in tables if name.lower() == str(table).lower()), None)
    if match is None:
        raise CompanyFileError("That file is not part of this data source.")
    return match


def remove_table(database_file: Path, table: str) -> str:
    with _working_copy(database_file) as (working, manifest):
        tables = user_tables(working)
        name = _require_table(tables, table)
        if len(tables) <= 1:
            raise CompanyFileError("This is the only file in this data source. Delete the data source instead.")
        with closing(sqlite3.connect(working)) as connection:
            connection.execute(f"DROP TABLE {_quote(name)}")
            connection.commit()
            connection.execute("VACUUM")
        manifest["files"].pop(name, None)
        manifest["reports"] = [item for item in manifest["reports"] if item.get("table") != name]
    return name


def replace_with_tally(
    database_file: Path,
    table: str,
    report: tally_import.TallyReport,
    *,
    source_file: str | None = None,
    allow_other_company: bool = False,
) -> tally_import.SaveResult:
    with _working_copy(database_file) as (working, manifest):
        name = _require_table(user_tables(working), table)
        others = {**manifest, "reports": [item for item in manifest["reports"] if item.get("table") != name]}
        if not allow_other_company and is_different_company(others, report.metadata.company_name):
            raise CompanyMismatchError(
                "This report is from a different company than the data already saved here. "
                "Confirm that you want to use it anyway."
            )
        with closing(sqlite3.connect(working)) as connection:
            connection.execute(f"DROP TABLE {_quote(name)}")
            connection.commit()
        result = _save_tally(working, report, name)
        _record_tally(manifest, report, result, table=name, label=None, source_file=source_file, replace_existing=True)
    return replace(result, table=name)


def replace_with_frame(
    database_file: Path, table: str, frame: pd.DataFrame, *, source_file: str | None = None
) -> int:
    with _working_copy(database_file) as (working, manifest):
        name = _require_table(user_tables(working), table)
        if frame.empty and len(frame.columns) == 0:
            raise CompanyFileError("The uploaded spreadsheet has no queryable columns.")
        with closing(sqlite3.connect(working)) as connection:
            connection.execute(f"DROP TABLE {_quote(name)}")
            rows = _write_frame(connection, name, frame)
            connection.commit()
        _record_table(manifest, name, label=None, source_file=source_file, replace_existing=True)
    return rows


# --------------------------------------------------------------------------
# Moving a whole source into a company
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MergeResult:
    tables: dict[str, str]
    rows: int


def merge_preview(target_file: Path, source_file: Path) -> dict[str, Any]:
    target_tables = user_tables(target_file)
    source_manifest = read_manifest(source_file)
    target_manifest = read_manifest(target_file)
    taken = list(target_tables)
    renames: dict[str, str] = {}
    for table in user_tables(source_file):
        new_name = unique_table_name(table, taken)
        taken.append(new_name)
        renames[table] = new_name
    source_companies = known_companies(source_manifest)
    different = any(is_different_company(target_manifest, name) for name in source_companies)
    return {"tables": renames, "different_company": different}


def merge_into(target_file: Path, source_file: Path, *, allow_other_company: bool = False) -> MergeResult:
    """Copy every table of ``source_file`` into ``target_file`` (renaming clashes)."""

    source_file = Path(source_file)
    if not source_file.is_file():
        raise CompanyFileError("The data source to move is no longer available. Re-upload it first.")
    if Path(source_file).resolve() == Path(target_file).resolve():
        raise CompanyFileError("Pick a different company to move this data source into.")
    source_manifest = read_manifest(source_file)
    with _working_copy(target_file) as (working, manifest):
        if not allow_other_company and any(
            is_different_company(manifest, name) for name in known_companies(source_manifest)
        ):
            raise CompanyMismatchError(
                "That data source is from a different company. Confirm that you want to move it anyway."
            )
        taken = user_tables(working)
        renames: dict[str, str] = {}
        rows = 0
        with closing(sqlite3.connect(working)) as connection:
            connection.execute("ATTACH DATABASE ? AS incoming", (f"file:{source_file.resolve()}?mode=ro",))
            try:
                incoming = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM incoming.sqlite_master WHERE type = 'table' "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
                    )
                ]
                if not incoming:
                    raise CompanyFileError("That data source has no tables to move.")
                for table in incoming:
                    new_name = unique_table_name(table, taken)
                    taken.append(new_name)
                    renames[table] = new_name
                    columns = connection.execute(f"PRAGMA incoming.table_info({_quote(table)})").fetchall()
                    definition = ", ".join(
                        f"{_quote(str(column[1]))} {column[2] or ''}".rstrip() for column in columns
                    )
                    connection.execute(f"CREATE TABLE main.{_quote(new_name)} ({definition})")
                    names = ", ".join(_quote(str(column[1])) for column in columns)
                    connection.execute(
                        f"INSERT INTO main.{_quote(new_name)} ({names}) SELECT {names} FROM incoming.{_quote(table)}"
                    )
                    rows += int(connection.execute(f"SELECT COUNT(*) FROM main.{_quote(new_name)}").fetchone()[0])
                connection.commit()
            finally:
                connection.execute("DETACH DATABASE incoming")
        now = _now()
        for item in source_manifest["reports"]:
            if item.get("table") in renames:
                manifest["reports"].append({**item, "table": renames[item["table"]]})
        for old, new in renames.items():
            entry = dict(source_manifest["files"].get(old) or {})
            if not entry:
                reports = [item for item in source_manifest["reports"] if item.get("table") == old]
                entry = {
                    "kind": FILE_KIND_TALLY if reports else FILE_KIND_TABLE,
                    "label": (reports[-1].get("report_name") if reports else old),
                    "added_at": (reports[0].get("imported_at") if reports else now),
                }
                if reports:
                    entry["report_type"] = reports[-1].get("report_type")
                    entry["report_name"] = reports[-1].get("report_name")
            entry["updated_at"] = now
            entry["moved_at"] = now
            manifest["files"][new] = entry
    return MergeResult(renames, rows)


__all__ = [
    "APPEND",
    "NEW",
    "CompanyFileError",
    "CompanyMismatchError",
    "MergeResult",
    "add_frame_groups",
    "add_frames",
    "add_tally_report",
    "append_overlap",
    "count_tables",
    "discard_company_file",
    "is_different_company",
    "known_companies",
    "list_files",
    "merge_into",
    "merge_preview",
    "new_company_file",
    "normalize_company_name",
    "read_manifest",
    "remove_table",
    "replace_with_frame",
    "replace_with_tally",
    "safe_table_name",
    "save_first_frames",
    "save_first_tally",
    "snapshot_for_rollback",
    "table_report_types",
    "tally_choices",
    "unique_table_name",
    "user_tables",
]
