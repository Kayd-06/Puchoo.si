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
from apps.core.tally_import import TallyImportError
from apps.core.workspaces import (
    TALLY_SOURCE_TYPE,
    UploadInspection,
    create_header_row_workspace,
    create_tally_workspace,
    extend_tally_workspace,
    inspect_upload,
    tally_overlap,
    create_server_workspace,
    create_sqlite_workspace,
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
PUBLIC_WORKSPACE_FIELDS = ("id", "name", "dialect", "source_type")
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

    return {field: workspace[field] for field in PUBLIC_WORKSPACE_FIELDS if field in workspace}

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
    user=Depends(require_data_manager),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    filename = file.filename or "uploaded_file"
    contents = await _read_upload(file)
    suffix = _validated_upload_kind(filename, contents)
    ws_name = name.strip() or filename.rsplit(".", 1)[0]
    
    try:
        inspection = inspect_upload(filename, contents)
        if inspection.kind == "tally":
            # Older clients skip the preview; the importer still keeps the
            # company header out of the table.
            workspace, _ = create_tally_workspace(ws_name, inspection.report)
        else:
            workspace = create_tabular_workspace(ws_name, filename, contents)

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
    except ValueError as exc:
        logger.info("upload rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The uploaded file could not be processed.") from None

@router.post("/upload-multiple")
async def upload_multiple_files(
    files: List[UploadFile] = File(...),
    name: str = Form(""),
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
    try:
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
    except ValueError as exc:
        logger.info("multiple upload rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The uploaded files could not be processed.") from None

def _import_target(target_workspace_id: str, user) -> Dict[str, Any] | None:
    target = target_workspace_id.strip()
    if not target:
        return None
    workspace = session_manager.get_workspace(target)
    if not workspace or workspace.get("owner_user_id") != _owner_id(user):
        raise HTTPException(status_code=404, detail="Workspace not found or unauthorized")
    if workspace.get("source_type") != TALLY_SOURCE_TYPE:
        raise HTTPException(status_code=400, detail="Only an imported Tally data source can take more Tally reports.")
    return workspace


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
    target = _import_target(target_workspace_id, user)
    inspection = _inspect(filename, contents)
    if inspection.kind == "tally":
        preview = inspection.report.preview()
        if target is not None:
            try:
                preview["append"] = tally_overlap(target, inspection.report)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        return preview
    if inspection.kind == "unknown":
        return {"kind": "unknown", "rows": inspection.rows, "suggested_header_row": inspection.suggested_header_row}
    return {"kind": "table"}


@router.post("/import/confirm")
async def confirm_import(
    file: UploadFile = File(...),
    name: str = Form(""),
    header_row: str = Form(""),
    target_workspace_id: str = Form(""),
    user=Depends(require_data_manager),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Save a previewed Tally report (new source or appended) or a header-row import."""

    filename = file.filename or "uploaded_file"
    contents = await _read_upload(file)
    _validated_upload_kind(filename, contents)
    chosen_header = _header_row(header_row)
    target = _import_target(target_workspace_id, user)
    try:
        if chosen_header is not None:
            if target is not None:
                raise HTTPException(status_code=400, detail="A header-row import creates a new data source.")
            workspace = create_header_row_workspace(
                name.strip() or filename.rsplit(".", 1)[0], filename, contents, chosen_header
            )
            summary: Dict[str, Any] = {"kind": "table"}
        else:
            inspection = _inspect(filename, contents)
            if inspection.kind != "tally":
                raise HTTPException(status_code=400, detail="This file is not a recognised Tally report.")
            report = inspection.report
            if target is not None:
                result = extend_tally_workspace(target, report)
                session_manager.set_schema(target["id"], None)
                create_notification(db, user_id=user.id, kind="workspace_uploaded", title="Tally report added",
                                    body=f"{report.metadata.report_name} added to {target.get('name', 'your data source')}.",
                                    resource_id=target["id"])
                db.commit()
                return {
                    "workspace": _public_workspace(target),
                    "import": {"kind": "tally", "table": result.table, "rows_added": result.rows_added,
                               "rows_skipped": result.rows_skipped, "rows_replaced": result.rows_replaced},
                }
            default_name = report.metadata.report_name
            workspace, result = create_tally_workspace(name.strip() or default_name, report)
            summary = {"kind": "tally", "table": result.table, "rows_added": result.rows_added,
                       "rows_skipped": result.rows_skipped, "rows_replaced": result.rows_replaced}
        ws_dict = workspace.as_dict()
        ws_dict["owner_user_id"] = _owner_id(user)
        session_manager.add_workspace(ws_dict)
        create_notification(db, user_id=user.id, kind="workspace_uploaded", title="Data source added",
                            body=f"{workspace.name} is ready for read-only questions.", resource_id=workspace.id)
        db.commit()
        return {"workspace": _public_workspace(ws_dict), "import": summary}
    except TallyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except UploadLimitError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except ValueError as exc:
        logger.info("import confirm rejected type=%s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="The uploaded file could not be processed.") from None


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
    local_source = workspace.get("source_type") in {"spreadsheet", "spreadsheet_collection", "file"}
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
