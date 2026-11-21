"""Workspaces router for managing database connections and schemas."""

import logging
from pathlib import Path
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from backend.routers.auth import current_user
from backend.database import get_db
from backend.notifications import create_notification
from backend.rate_limit import connection_limiter
from backend.config import settings

from apps.core.db_connection import (
    GENERIC_CONNECTION_ERROR,
    GENERIC_HOST_ERRORS,
    DatabaseConnectionError,
    ServerConnection,
)
from apps.core import company_files
from apps.core.company_files import CompanyFileError, CompanyMismatchError
from apps.core.tally_import import TallyImportError
from apps.core.workspaces import (
    TALLY_SOURCE_TYPE,
    UploadInspection,
    add_header_row_to_company,
    add_tally_to_company,
    company_tally_preview,
    company_workspace_file,
    create_header_row_workspace,
    create_tally_workspace,
    first_frame,
    header_row_frame,
    inspect_upload,
    tabular_frames,
    workspace_source_file,
    create_server_workspace,
    create_sqlite_workspace,
    delete_workspace_files,
    create_tabular_workspace,
    create_tabular_collection_workspace,
    get_schema_metrics,
    get_schema_snapshot,
    get_schema_table_stats,
)
from apps.core.upload_limits import MAX_INPUT_BYTES, UploadLimitError
from apps.api.session import session_manager
from apps.api.security import (
    require_capability,
    require_connection_admin,
    require_data_manager,
    require_workspace_owner,
    require_workspace_editor,
    tenant_owner_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
PUBLIC_WORKSPACE_FIELDS = ("id", "name", "dialect", "source_type", "availability")
COMPANY_MISMATCH_CODE = "company_mismatch"
MAX_UPLOAD_BYTES = settings.upload_max_bytes
MAX_UPLOAD_FILES = settings.upload_max_files
OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
NUMBERS_FILE_MESSAGE = (
    "Apple Numbers files can't be read. In Numbers choose File > Export To > Excel or CSV. "
    "From Tally, press Alt+E (Export), choose Excel, and upload that file."
)
TALLY_MULTI_UPLOAD_MESSAGE = "Upload Tally reports one at a time so each one can be checked before it is saved."


async def _read_upload(file: UploadFile, remaining: int = MAX_INPUT_BYTES) -> bytes:
    """Bound a multipart part before it can exhaust the application process."""

    budget = min(MAX_UPLOAD_BYTES, MAX_INPUT_BYTES, remaining)
    contents = await file.read(budget + 1)
    if len(contents) > budget:
        raise HTTPException(status_code=413, detail="The uploaded file is too large.")
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return contents


def _validated_upload_kind(filename: str, contents: bytes) -> str:
    """Verify the bytes agree with an accepted CSV or Excel format."""

    suffix = Path(filename).suffix.lower()
    if suffix == ".numbers":
        raise HTTPException(status_code=400, detail=NUMBERS_FILE_MESSAGE)
    if suffix == ".xlsx":
        valid = contents.startswith(b"PK\x03\x04")
    elif suffix == ".xls":
        raise HTTPException(status_code=400, detail="Legacy XLS is unsupported for safe parsing. Save as XLSX or CSV first.")
    elif suffix == ".csv":
        head = contents[:8192]
        if len(contents) > len(head):
            # Drop a multi-byte UTF-8 character that the 8 KiB cut may have split.
            head = head[: next((len(head) - 1 - i for i in range(3) if head[-1 - i] >= 0xC0), len(head))]
        try:
            sample = head.decode("utf-8")
            valid = bool(sample.strip()) and "\x00" not in sample and not sample.lstrip().lower().startswith(("<html", "<!doctype", "<?xml"))
        except UnicodeDecodeError:
            valid = False
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format.")
    if not valid:
        raise HTTPException(status_code=400, detail="The uploaded file content does not match its declared format.")
    return suffix

class ServerWorkspaceRequest(BaseModel):
    # str_strip_whitespace is intentionally omitted: it would silently strip
    # leading/trailing spaces from `password`, causing DB connections to fail
    # with a generic error. If `name` trimming is needed, use a field_validator.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, strict=True)
    engine: str = Field(strict=True)
    host: str = Field(min_length=1, max_length=253, strict=True)
    port: int = Field(ge=1, le=65_535, strict=True)
    database: str = Field(min_length=1, max_length=128, strict=True)
    username: str = Field(min_length=1, max_length=128, strict=True)
    password: str = Field(min_length=1, max_length=1_024, strict=True)
    # Accepted so older clients still validate. TLS is a server setting.
    ssl_required: bool = Field(default=True, strict=True)

    @field_validator("engine")
    @classmethod
    def known_engine(cls, value: str) -> str:
        if value not in {"postgresql", "mysql"}:
            raise ValueError("Dialect must be postgresql or mysql.")
        return value

    @model_validator(mode="before")
    @classmethod
    def connection_fields(cls, data: object) -> object:
        """Validate the raw body so port strings and booleans are not coerced first."""

        if not isinstance(data, dict):
            return data
        try:
            ServerConnection(
                dialect=data.get("engine"),
                host=data.get("host"),
                port=data.get("port"),
                database=data.get("database"),
                username=data.get("username"),
                password=data.get("password"),
            )
        except ValidationError as exc:
            message = str(exc.errors()[0].get("msg", "Check the connection details and try again."))
            prefix = "Value error, "
            if message.startswith(prefix):
                message = message[len(prefix):]
            raise ValueError(message) from None
        return data

def _owner_id(user) -> str:
    return tenant_owner_id(user)


def _public_workspace(workspace: Dict[str, Any]) -> Dict[str, Any]:
    """Never send database URIs (which may embed passwords) to a browser."""

    public = {field: workspace[field] for field in PUBLIC_WORKSPACE_FIELDS if field in workspace}
    if workspace.get("source_type") != "server" and workspace.get("availability") != "unavailable":
        count = company_files.count_tables(workspace_source_file(workspace.get("database_uri")))
        if count is not None:
            public["file_count"] = count
    return public


def _flag(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _mismatch(exc: CompanyMismatchError) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": COMPANY_MISMATCH_CODE, "message": str(exc)})


def _company_target(target_workspace_id: str, user) -> Dict[str, Any] | None:
    """An uploaded company in the caller's tenant that files can be added to."""

    target = target_workspace_id.strip()
    if not target:
        return None
    workspace = session_manager.get_workspace(target)
    if not workspace or workspace.get("owner_user_id") != _owner_id(user):
        raise HTTPException(status_code=404, detail="Workspace not found or unauthorized")
    if workspace.get("source_type") == "server":
        raise HTTPException(
            status_code=400,
            detail="A database connection can't take uploaded files. Pick an uploaded company or create a new one.",
        )
    workspace = session_manager.ensure_source_on_disk(target, _owner_id(user)) or workspace
    if workspace.get("availability") == "unavailable" or not workspace.get("database_uri"):
        raise HTTPException(status_code=409, detail="This company needs to be uploaded again before files can be added.")
    return workspace


def _apply_company_change(workspace: Dict[str, Any], user, change, *, drop_pending: bool = False):
    """Run one file change on a company, refresh its stored copy, undo the file on failure."""

    tenant_id = _owner_id(user)
    try:
        path = company_workspace_file(workspace)
    except CompanyFileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    with company_files.snapshot_for_rollback(path) as restore:
        outcome = change(workspace)
        try:
            session_manager.source_changed(workspace["id"], tenant_id, drop_pending=drop_pending)
        except Exception:
            restore()
            raise
    return outcome

@router.get("/")
def list_workspaces(user=Depends(require_capability("view_workspaces"))) -> List[Dict[str, Any]]:
    owner_id = _owner_id(user)
    return [
        _public_workspace(workspace)
        for workspace in session_manager.list_tenant_workspaces(owner_id)
        if workspace.get("owner_user_id") == owner_id
    ]

def _source_connect_args(workspace: Dict[str, Any]) -> dict | None:
    value = workspace.get("connect_args")
    return value if isinstance(value, dict) else None


@router.post("/server")
def connect_server(request: ServerWorkspaceRequest, user=Depends(require_connection_admin), db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not connection_limiter.allow([f"db-connect:{user.id}"]):
        raise HTTPException(
            status_code=429,
            detail="Too many connection attempts. Try again later.",
            headers={"Retry-After": str(settings.connection_rate_window_seconds)},
        )
    try:
        workspace = create_server_workspace(
            request.name,
            engine=request.engine,  # type: ignore[arg-type]
            host=request.host,
            port=request.port,
            database=request.database,
            username=request.username,
            password=request.password,
        )
        ws_dict = workspace.as_dict()
        ws_dict["owner_user_id"] = _owner_id(user)
        session_manager.add_workspace(ws_dict)
        create_notification(db, user_id=user.id, kind="workspace_connected", title="Database connected", body=f"{workspace.name} is ready for read-only questions.", resource_id=workspace.id)
        db.commit()
        return _public_workspace(ws_dict)
    except DatabaseConnectionError:
        raise HTTPException(status_code=400, detail=GENERIC_CONNECTION_ERROR) from None
    except SQLAlchemyError as exc:
        logger.warning("database connection failed: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail=GENERIC_CONNECTION_ERROR) from None
    except ValueError as exc:
        if str(exc) in GENERIC_HOST_ERRORS:
            logger.info("database host rejected")
            raise HTTPException(status_code=400, detail=GENERIC_CONNECTION_ERROR) from None
        logger.info("database connection input rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="Check the connection details and try again.") from None

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    name: str = Form(""),
    target_workspace_id: str = Form(""),
    table_name: str = Form(""),
    user=Depends(require_data_manager),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    filename = file.filename or "uploaded_file"
    contents = await _read_upload(file)
    suffix = _validated_upload_kind(filename, contents)
    ws_name = name.strip() or filename.rsplit(".", 1)[0]
    target = _company_target(target_workspace_id, user)

    try:
        inspection = inspect_upload(filename, contents)
        if target is not None:
            if inspection.kind == "tally":
                raise HTTPException(status_code=400, detail="Tally reports are checked first. Use the preview to add them.")
            tables = _apply_company_change(
                target, user,
                lambda workspace: company_files.add_frames(
                    company_workspace_file(workspace), tabular_frames(filename, contents),
                    base_name=table_name.strip() or Path(filename).stem,
                    label=Path(filename).stem, source_file=filename,
                ),
            )
            create_notification(db, user_id=user.id, kind="workspace_uploaded", title="File added",
                                body=f"{Path(filename).stem} added to {target.get('name', 'your company')}.",
                                resource_id=target["id"])
            db.commit()
            return {**_public_workspace(session_manager.get_workspace(target["id"]) or target), "tables_added": tables}
        if inspection.kind == "tally":
            # Older clients skip the preview; the importer still keeps the
            # company header out of the table.
            workspace, _ = create_tally_workspace(ws_name, inspection.report, source_file=filename)
        else:
            workspace = create_tabular_workspace(ws_name, filename, contents, table_name=table_name.strip())

        ws_dict = workspace.as_dict()
        ws_dict["owner_user_id"] = _owner_id(user)
        session_manager.add_workspace(ws_dict)
        create_notification(db, user_id=user.id, kind="workspace_uploaded", title="Data source added", body=f"{workspace.name} is ready for read-only questions.", resource_id=workspace.id)
        db.commit()
        return _public_workspace(ws_dict)
    except UploadLimitError as exc:
        logger.info("upload rejected by resource limit status=%s", exc.status_code)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except TallyImportError as exc:
        logger.info("tally upload rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except CompanyFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError as exc:
        logger.info("upload rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The uploaded file could not be processed.") from None

@router.post("/upload-multiple")
async def upload_multiple_files(
    files: List[UploadFile] = File(...),
    name: str = Form(""),
    target_workspace_id: str = Form(""),
    user=Depends(require_data_manager),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one CSV or Excel file")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail="Too many files were uploaded at once.")
    payloads: list[tuple[str, bytes]] = []
    remaining = MAX_INPUT_BYTES
    for file in files:
        filename = file.filename or "uploaded_file"
        contents = await _read_upload(file, remaining)
        remaining -= len(contents)
        suffix = _validated_upload_kind(filename, contents)
        try:
            is_tally = inspect_upload(filename, contents).kind == "tally"
        except (UploadLimitError, ValueError):
            is_tally = False  # The collection importer reports the real error below.
        if is_tally:
            raise HTTPException(status_code=400, detail=TALLY_MULTI_UPLOAD_MESSAGE)
        payloads.append((filename, contents))
    workspace_name = name.strip() or f"{len(payloads)}-file workspace"
    target = _company_target(target_workspace_id, user)
    try:
        if target is not None:
            groups = [
                (tabular_frames(filename, contents), Path(filename).stem, Path(filename).stem, filename)
                for filename, contents in payloads
            ]
            tables = _apply_company_change(
                target, user,
                lambda workspace: company_files.add_frame_groups(company_workspace_file(workspace), groups),
            )
            create_notification(db, user_id=user.id, kind="workspace_uploaded", title="Files added",
                                body=f"{len(payloads)} files added to {target.get('name', 'your company')}.",
                                resource_id=target["id"])
            db.commit()
            return {**_public_workspace(session_manager.get_workspace(target["id"]) or target), "tables_added": tables}
        workspace = create_tabular_collection_workspace(workspace_name, payloads)
        ws_dict = workspace.as_dict()
        ws_dict["owner_user_id"] = _owner_id(user)
        session_manager.add_workspace(ws_dict)
        create_notification(db, user_id=user.id, kind="workspace_uploaded", title="Data sources added", body=f"{workspace.name} is ready for read-only questions.", resource_id=workspace.id)
        db.commit()
        return _public_workspace(ws_dict)
    except UploadLimitError as exc:
        logger.info("multiple upload rejected by resource limit status=%s", exc.status_code)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except CompanyFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError as exc:
        logger.info("multiple upload rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The uploaded files could not be processed.") from None

def _inspect(filename: str, contents: bytes) -> UploadInspection:
    try:
        return inspect_upload(filename, contents)
    except UploadLimitError as exc:
        logger.info("import preview rejected by resource limit status=%s", exc.status_code)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except TallyImportError as exc:
        logger.info("tally preview rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError:
        logger.info("import preview rejected")
        raise HTTPException(status_code=400, detail="The uploaded file could not be processed.") from None


def _header_row(value: str) -> int | None:
    if not value.strip():
        return None
    try:
        row = int(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Choose a header row from the preview.") from None
    if not 0 <= row < 15:
        raise HTTPException(status_code=400, detail="Choose a header row from the preview.")
    return row


@router.post("/import/preview")
async def preview_import(
    file: UploadFile = File(...),
    target_workspace_id: str = Form(""),
    user=Depends(require_data_manager),
) -> Dict[str, Any]:
    """Show what an upload will become before anything is saved.

    Nothing is written to disk. The browser keeps the file and sends it again
    to /import/confirm, so the server never holds an unconfirmed upload.
    """

    filename = file.filename or "uploaded_file"
    contents = await _read_upload(file)
    _validated_upload_kind(filename, contents)
    target = _company_target(target_workspace_id, user)
    inspection = _inspect(filename, contents)
    existing: list[str] = []
    if target is not None:
        try:
            existing = company_files.user_tables(company_workspace_file(target))
        except (CompanyFileError, OSError):
            existing = []
    suggested_plain = company_files.unique_table_name(Path(filename).stem, existing, "sheet")
    if inspection.kind == "tally":
        preview = inspection.report.preview()
        try:
            choices = company_tally_preview(target, inspection.report)
        except CompanyFileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        preview["company"] = choices
        if target is not None:
            preview["append"] = {
                "existing_vouchers": choices["existing_vouchers"] if choices["suggested_mode"] == "append" else 0,
                "new_rows": choices["new_rows"] if choices["suggested_mode"] == "append" else len(inspection.report.rows),
                "different_company": choices["different_company"],
            }
        return preview
    company = {"tables": existing, "suggested_table": suggested_plain}
    if inspection.kind == "unknown":
        return {"kind": "unknown", "rows": inspection.rows, "suggested_header_row": inspection.suggested_header_row,
                **({"company": company} if target is not None else {})}
    return {"kind": "table", **({"company": company} if target is not None else {})}


@router.post("/import/append-check")
async def append_check(
    file: UploadFile = File(...),
    target_workspace_id: str = Form(...),
    table_name: str = Form(...),
    user=Depends(require_data_manager),
) -> Dict[str, Any]:
    """How many rows adding a Tally report to one chosen table would add or skip."""

    filename = file.filename or "uploaded_file"
    contents = await _read_upload(file)
    _validated_upload_kind(filename, contents)
    target = _company_target(target_workspace_id, user)
    inspection = _inspect(filename, contents)
    if inspection.kind != "tally" or target is None:
        raise HTTPException(status_code=400, detail="This file is not a recognised Tally report.")
    path = company_workspace_file(target)
    tables = company_files.user_tables(path)
    match = next((name for name in tables if name.lower() == table_name.strip().lower()), None)
    if match is None:
        raise HTTPException(status_code=400, detail="That table is not part of this company.")
    return company_files.append_overlap(path, inspection.report, match)


@router.post("/import/confirm")
async def confirm_import(
    file: UploadFile = File(...),
    name: str = Form(""),
    header_row: str = Form(""),
    target_workspace_id: str = Form(""),
    table_name: str = Form(""),
    mode: str = Form(""),
    label: str = Form(""),
    confirm_company_mismatch: str = Form(""),
    user=Depends(require_data_manager),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Save a previewed file: a new company, or one more file inside an existing company."""

    filename = file.filename or "uploaded_file"
    contents = await _read_upload(file)
    _validated_upload_kind(filename, contents)
    chosen_header = _header_row(header_row)
    if mode not in {"", company_files.APPEND, company_files.NEW}:
        raise HTTPException(status_code=400, detail="Choose whether to add rows to a table or create a new table.")
    target = _company_target(target_workspace_id, user)
    file_label = label.strip() or None
    try:
        if target is not None:
            if chosen_header is not None:
                tables = _apply_company_change(
                    target, user,
                    lambda workspace: add_header_row_to_company(
                        workspace, filename, contents, chosen_header,
                        table_name=table_name.strip(), label=file_label,
                    ),
                )
                summary: Dict[str, Any] = {"kind": "table", "table": tables[0], "tables": tables}
                title, body = "File added", f"{file_label or Path(filename).stem} added to {target.get('name', 'your company')}."
            else:
                inspection = _inspect(filename, contents)
                if inspection.kind == "tally":
                    report = inspection.report
                    result = _apply_company_change(
                        target, user,
                        lambda workspace: add_tally_to_company(
                            workspace, report, mode=mode, table_name=table_name.strip(), label=file_label,
                            source_file=filename, allow_other_company=_flag(confirm_company_mismatch),
                        ),
                    )
                    summary = {"kind": "tally", "table": result.table, "rows_added": result.rows_added,
                               "rows_skipped": result.rows_skipped, "rows_replaced": result.rows_replaced}
                    title, body = "Tally report added", f"{report.metadata.report_name} added to {target.get('name', 'your company')}."
                elif inspection.kind == "table":
                    tables = _apply_company_change(
                        target, user,
                        lambda workspace: company_files.add_frames(
                            company_workspace_file(workspace), tabular_frames(filename, contents),
                            base_name=table_name.strip() or Path(filename).stem,
                            label=file_label or Path(filename).stem, source_file=filename,
                        ),
                    )
                    summary = {"kind": "table", "table": tables[0], "tables": tables}
                    title, body = "File added", f"{Path(filename).stem} added to {target.get('name', 'your company')}."
                else:
                    raise HTTPException(status_code=400, detail="Choose the header row for this file first.")
            create_notification(db, user_id=user.id, kind="workspace_uploaded", title=title, body=body,
                                resource_id=target["id"])
            db.commit()
            current = session_manager.get_workspace(target["id"]) or target
            return {"workspace": _public_workspace(current), "import": summary, "added_to_existing": True}

        company_name = name.strip()
        if chosen_header is not None:
            workspace = create_header_row_workspace(
                company_name or filename.rsplit(".", 1)[0], filename, contents, chosen_header,
                table_name=table_name.strip(),
            )
            summary = {"kind": "table"}
        else:
            inspection = _inspect(filename, contents)
            if inspection.kind == "tally":
                report = inspection.report
                workspace, result = create_tally_workspace(
                    company_name or report.metadata.report_name, report,
                    table_name=table_name.strip(), label=file_label, source_file=filename,
                )
                summary = {"kind": "tally", "table": result.table, "rows_added": result.rows_added,
                           "rows_skipped": result.rows_skipped, "rows_replaced": result.rows_replaced}
            elif inspection.kind == "table":
                workspace = create_tabular_workspace(
                    company_name or filename.rsplit(".", 1)[0], filename, contents, table_name=table_name.strip()
                )
                summary = {"kind": "table"}
            else:
                raise HTTPException(status_code=400, detail="This file is not a recognised Tally report.")
        ws_dict = workspace.as_dict()
        ws_dict["owner_user_id"] = _owner_id(user)
        session_manager.add_workspace(ws_dict)
        create_notification(db, user_id=user.id, kind="workspace_uploaded", title="Data source added",
                            body=f"{workspace.name} is ready for read-only questions.", resource_id=workspace.id)
        db.commit()
        return {"workspace": _public_workspace(ws_dict), "import": summary}
    except CompanyMismatchError as exc:
        raise _mismatch(exc) from None
    except CompanyFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except TallyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except UploadLimitError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except ValueError as exc:
        logger.info("import confirm rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The uploaded file could not be processed.") from None


@router.post("/{workspace_id}/reupload")
async def reupload_source(
    workspace_id: str,
    file: UploadFile = File(...),
    header_row: str = Form(""),
    user=Depends(require_data_manager),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Replace the file behind an existing source, keeping its id, name, history and guardrails.

    Uses the same role check, size limits and format checks as a new upload.
    """

    tenant_id = _owner_id(user)
    existing = session_manager.get_workspace(workspace_id)
    if not existing or existing.get("owner_user_id") != tenant_id:
        raise HTTPException(status_code=404, detail="Workspace not found or unauthorized")
    if existing.get("source_type") == "server":
        raise HTTPException(status_code=400, detail="A database connection can't be re-uploaded. Delete it and connect it again.")
    filename = file.filename or "uploaded_file"
    contents = await _read_upload(file)
    _validated_upload_kind(filename, contents)
    chosen_header = _header_row(header_row)
    name = str(existing.get("name") or filename.rsplit(".", 1)[0])
    try:
        if chosen_header is not None:
            replacement = create_header_row_workspace(name, filename, contents, chosen_header)
        else:
            inspection = inspect_upload(filename, contents)
            if inspection.kind == "tally":
                replacement, _ = create_tally_workspace(name, inspection.report)
            elif existing.get("source_type") == TALLY_SOURCE_TYPE:
                raise HTTPException(status_code=400, detail="This source was a Tally report. Upload the Tally export again.")
            else:
                replacement = create_tabular_workspace(name, filename, contents)
    except UploadLimitError as exc:
        logger.info("reupload rejected by resource limit status=%s", exc.status_code)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except TallyImportError as exc:
        logger.info("tally reupload rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError as exc:
        logger.info("reupload rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The uploaded file could not be processed.") from None
    try:
        updated = session_manager.replace_source(workspace_id, tenant_id, replacement.as_dict())
    except ValueError:
        delete_workspace_files(replacement.database_uri)
        raise HTTPException(status_code=404, detail="Workspace not found or unauthorized") from None
    except Exception:
        delete_workspace_files(replacement.database_uri)
        raise
    create_notification(db, user_id=user.id, kind="workspace_uploaded", title="Data source re-uploaded",
                        body=f"{updated.get('name', 'Your data source')} is ready for read-only questions again.",
                        resource_id=workspace_id)
    db.commit()
    return _public_workspace(updated)


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_workspace_id: str = Field(min_length=1, max_length=100, strict=True)
    confirm_company_mismatch: bool = Field(default=False, strict=True)


def _company_for_change(workspace_id: str, user) -> Dict[str, Any]:
    """Owner/admin access to one uploaded company in the caller's tenant, on disk."""

    tenant_id = _owner_id(user)
    workspace = session_manager.get_workspace(workspace_id)
    if not workspace or workspace.get("owner_user_id") != tenant_id:
        raise HTTPException(status_code=404, detail="Workspace not found or unauthorized")
    if workspace.get("source_type") == "server":
        raise HTTPException(status_code=400, detail="A database connection has no uploaded files to change.")
    workspace = session_manager.ensure_source_on_disk(workspace_id, tenant_id) or workspace
    if workspace.get("availability") == "unavailable" or not workspace.get("database_uri"):
        raise HTTPException(status_code=409, detail="This data source needs to be uploaded again first.")
    return workspace


@router.get("/{workspace_id}/files")
def list_company_files(workspace_id: str, workspace: Dict = Depends(require_workspace_editor)) -> Dict[str, Any]:
    """The files (one table each) inside a company, with rows, period and dates."""

    if workspace.get("source_type") == "server":
        return {"workspace_id": workspace_id, "source_type": "server", "files": []}
    try:
        files = company_files.list_files(company_workspace_file(workspace))
    except CompanyFileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except (OSError, ValueError) as exc:
        logger.warning("company files listing failed type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The files in this data source could not be listed.") from None
    return {"workspace_id": workspace_id, "source_type": workspace.get("source_type"), "files": files}


@router.delete("/{workspace_id}/files/{table}")
def remove_company_file(
    workspace_id: str,
    table: str,
    user=Depends(require_data_manager),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    workspace = _company_for_change(workspace_id, user)
    try:
        removed = _apply_company_change(
            workspace, user,
            lambda current: company_files.remove_table(company_workspace_file(current), table),
            drop_pending=True,
        )
    except CompanyFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    create_notification(db, user_id=user.id, kind="workspace_uploaded", title="File removed",
                        body=f"A file was removed from {workspace.get('name', 'your company')}.",
                        resource_id=workspace_id)
    db.commit()
    return {"removed": removed, "workspace": _public_workspace(session_manager.get_workspace(workspace_id) or workspace)}


@router.post("/{workspace_id}/files/{table}/replace")
async def replace_company_file(
    workspace_id: str,
    table: str,
    file: UploadFile = File(...),
    header_row: str = Form(""),
    confirm_company_mismatch: str = Form(""),
    user=Depends(require_data_manager),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Re-upload just one file of a company. Other files are not touched."""

    workspace = _company_for_change(workspace_id, user)
    filename = file.filename or "uploaded_file"
    contents = await _read_upload(file)
    _validated_upload_kind(filename, contents)
    chosen_header = _header_row(header_row)
    try:
        if chosen_header is not None:
            frame = header_row_frame(filename, contents, chosen_header)
            rows = _apply_company_change(
                workspace, user,
                lambda current: company_files.replace_with_frame(
                    company_workspace_file(current), table, frame, source_file=filename),
                drop_pending=True,
            )
            summary: Dict[str, Any] = {"kind": "table", "table": table, "rows": rows}
        else:
            inspection = _inspect(filename, contents)
            if inspection.kind == "tally":
                report = inspection.report
                result = _apply_company_change(
                    workspace, user,
                    lambda current: company_files.replace_with_tally(
                        company_workspace_file(current), table, report, source_file=filename,
                        allow_other_company=_flag(confirm_company_mismatch),
                    ),
                    drop_pending=True,
                )
                summary = {"kind": "tally", "table": result.table, "rows": result.rows_added}
            elif inspection.kind == "table":
                frame = first_frame(filename, contents)
                rows = _apply_company_change(
                    workspace, user,
                    lambda current: company_files.replace_with_frame(
                        company_workspace_file(current), table, frame, source_file=filename),
                    drop_pending=True,
                )
                summary = {"kind": "table", "table": table, "rows": rows}
            else:
                return {"kind": "unknown", "rows": inspection.rows,
                        "suggested_header_row": inspection.suggested_header_row, "needs_header_row": True}
    except CompanyMismatchError as exc:
        raise _mismatch(exc) from None
    except CompanyFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except TallyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except UploadLimitError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except ValueError as exc:
        logger.info("file replace rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The uploaded file could not be processed.") from None
    create_notification(db, user_id=user.id, kind="workspace_uploaded", title="File replaced",
                        body=f"A file in {workspace.get('name', 'your company')} was replaced.",
                        resource_id=workspace_id)
    db.commit()
    return {"import": summary, "workspace": _public_workspace(session_manager.get_workspace(workspace_id) or workspace)}


@router.get("/{workspace_id}/merge-preview")
def merge_preview(workspace_id: str, source_workspace_id: str, user=Depends(require_data_manager)) -> Dict[str, Any]:
    if source_workspace_id == workspace_id:
        raise HTTPException(status_code=400, detail="Pick a different data source to move.")
    target = _company_for_change(workspace_id, user)
    source = _company_for_change(source_workspace_id, user)
    try:
        preview = company_files.merge_preview(company_workspace_file(target), company_workspace_file(source))
    except CompanyFileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    renamed = {old: new for old, new in preview["tables"].items() if old != new}
    return {"tables": preview["tables"], "renamed": renamed, "different_company": preview["different_company"]}


@router.post("/{workspace_id}/merge")
def merge_source_into_company(
    workspace_id: str,
    request: MergeRequest,
    user=Depends(require_data_manager),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Move every file of another uploaded source into this company, then remove that source.

    Owner/admin only, same tenant only, uploaded sources only. The target file
    is rebuilt on a private copy and swapped in; if the database commit fails
    the target file is put back and the moved source is left as it was.
    """

    tenant_id = _owner_id(user)
    if request.source_workspace_id == workspace_id:
        raise HTTPException(status_code=400, detail="Pick a different data source to move.")
    target = _company_for_change(workspace_id, user)
    source = _company_for_change(request.source_workspace_id, user)
    target_path = company_workspace_file(target)
    source_path = company_workspace_file(source)
    try:
        with company_files.snapshot_for_rollback(target_path) as restore:
            result = company_files.merge_into(
                target_path, source_path, allow_other_company=request.confirm_company_mismatch
            )
            try:
                moved_history = session_manager.absorb_source(
                    workspace_id, request.source_workspace_id, tenant_id, source_name=source.get("name"))
            except Exception:
                restore()
                raise
    except CompanyMismatchError as exc:
        raise _mismatch(exc) from None
    except CompanyFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError:
        raise HTTPException(status_code=404, detail="Workspace not found or unauthorized") from None
    create_notification(db, user_id=user.id, kind="workspace_uploaded", title="Data moved into company",
                        body=f"{source.get('name', 'A data source')} was moved into {target.get('name', 'your company')}.",
                        resource_id=workspace_id)
    db.commit()
    renamed = {old: new for old, new in result.tables.items() if old != new}
    return {
        "workspace": _public_workspace(session_manager.get_workspace(workspace_id) or target),
        "removed_workspace_id": request.source_workspace_id,
        "tables": result.tables,
        "renamed": renamed,
        "rows": result.rows,
        "history_moved": moved_history,
    }


@router.get("/{workspace_id}")
def get_workspace(workspace: Dict = Depends(require_workspace_editor)) -> Dict[str, Any]:
    return _public_workspace(workspace)

@router.delete("/{workspace_id}")
def delete_workspace(workspace_id: str, workspace: Dict = Depends(require_workspace_owner)) -> Response:
    session_manager.remove_workspace(workspace_id)
    return Response(status_code=204)

@router.get("/{workspace_id}/schema")
def get_schema(workspace_id: str, workspace: Dict = Depends(require_workspace_editor)) -> Dict[str, str]:
    schema = session_manager.get_schema(workspace_id)
    if schema is None:
        try:
            schema = get_schema_snapshot(workspace["database_uri"], connect_args=_source_connect_args(workspace))
            session_manager.set_schema(workspace_id, schema)
        except ValueError as exc:
            logger.exception("workspace schema inspection failed: %s", type(exc).__name__)
            raise HTTPException(status_code=400, detail="The workspace schema could not be inspected.") from None
    return {"schema": schema}


@router.post("/{workspace_id}/schema/refresh")
def refresh_schema(workspace_id: str, workspace: Dict = Depends(require_workspace_editor)) -> Dict[str, str]:
    try:
        schema = get_schema_snapshot(workspace["database_uri"], connect_args=_source_connect_args(workspace))
        session_manager.set_schema(workspace_id, schema)
        return {"schema": schema}
    except ValueError as exc:
        logger.exception("workspace schema refresh failed: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The workspace schema could not be refreshed.") from None

@router.get("/{workspace_id}/metrics")
def get_metrics(workspace_id: str, workspace: Dict = Depends(require_workspace_editor)) -> Dict[str, int]:
    try:
        return get_schema_metrics(workspace["database_uri"], connect_args=_source_connect_args(workspace))
    except ValueError as exc:
        logger.exception("workspace metrics failed: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The workspace metrics could not be loaded.") from None

@router.get("/{workspace_id}/tables")
def get_tables(workspace_id: str, workspace: Dict = Depends(require_workspace_editor)) -> List[Dict[str, Any]]:
    local_source = workspace.get("source_type") in {"spreadsheet", "spreadsheet_collection", "file", TALLY_SOURCE_TYPE}
    try:
        stats = get_schema_table_stats(
            workspace["database_uri"],
            include_row_counts=local_source,
            connect_args=_source_connect_args(workspace),
        )
        return stats # type: ignore
    except ValueError as exc:
        logger.exception("workspace table inspection failed: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The workspace tables could not be inspected.") from None
