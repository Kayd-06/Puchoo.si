"""Single-process API cache: SQL is authoritative; JSON is a legacy/checkpoint store.

All production mutations enter serialized commands. This is not a multi-worker store.
"""

import logging
import time
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from typing import Any, Dict, Iterable, List

from apps.core.local_state import STATE_LOCK, load_local_state, save_local_state

logger = logging.getLogger(__name__)


def serialized(method):
    @wraps(method)
    def call(*args, **kwargs):
        with STATE_LOCK:
            return method(*args, **kwargs)
    return call


class SessionManager:
    SOURCE_REFRESH_SECONDS = 30
    DEFAULT_GUARDRAILS = {
        "max_rows": 500,
        "timeout_seconds": 30,
        "confirm_complex_queries": False,
    }

    def __init__(self) -> None:
        saved = load_local_state()
        self.workspaces: Dict[str, Dict[str, Any]] = saved["workspaces"]
        self.workspace_guardrails: Dict[str, Dict[str, Any]] = {
            workspace_id: {**self.DEFAULT_GUARDRAILS, **guardrails}
            for workspace_id, guardrails in saved["workspace_guardrails"].items()
            if isinstance(guardrails, dict)
        }
        self.query_history: Dict[str, List[Dict[str, Any]]] = saved["query_history"]
        self.profile: Dict[str, str] = {
            "name": "",
            "email": "",
            "department": "",
            "timezone": "UTC",
            **saved["profile"],
        }
        self.schema_snapshots: Dict[str, str] = {}
        # Stores active proposal per workspace to avoid stale references
        self.active_proposals: Dict[str, Dict[str, Any]] = {}
        self._init_refresh_state()

    def _init_refresh_state(self) -> None:
        self._clock = time.monotonic
        self._tenant_refreshed_at: Dict[str, float] = {}
        self._source_generation = 0

    @contextmanager
    def _edit(self, workspace_id=None, db_action=None):
        """Stage under one owner; restore cache on SQL failure, checkpoint after commit."""
        with STATE_LOCK:
            names = ("workspaces", "workspace_guardrails", "query_history", "profile", "active_proposals", "schema_snapshots")
            previous = {name: getattr(self, name) for name in names}
            # Runtime connection arguments may contain an SSLContext (not pickleable).
            self.workspaces = {key: dict(value) for key, value in self.workspaces.items()}
            for name in names[1:]:
                setattr(self, name, deepcopy(getattr(self, name)))
            try:
                yield
                self.save(active_workspace_id=workspace_id, db_action=db_action)
            except Exception:
                for name, value in previous.items():
                    setattr(self, name, value)
                raise

    @serialized
    def get_workspace(self, workspace_id):
        workspace = self.workspaces.get(workspace_id)
        return dict(workspace) if workspace else None

    @serialized
    def list_workspaces(self):
        return [dict(workspace) for workspace in self.workspaces.values()]

    def _tenant_cache(self, tenant_id):
        return [dict(workspace) for workspace in self.workspaces.values()
                if workspace.get("owner_user_id") == tenant_id]

    @staticmethod
    def _keep_runtime_credentials(workspace, existing):
        """Server credentials deliberately live only in this process, never in SQL."""
        if (workspace.get("source_type") == "server"
                and existing.get("owner_user_id") == workspace.get("owner_user_id")
                and existing.get("database_uri")):
            for key in ("database_uri", "connect_args"):
                if key in existing:
                    workspace[key] = existing[key]
            workspace["availability"] = "available"
        return workspace

    def _invalidate_tenant_refresh(self, tenant_id=None):
        """Call with STATE_LOCK held whenever this process adds or removes a source."""
        self._source_generation += 1
        if tenant_id is None:
            self._tenant_refreshed_at.clear()
        else:
            self._tenant_refreshed_at.pop(tenant_id, None)

    def list_tenant_workspaces(self, tenant_id):
        """Refresh shared source discovery from SQL, at most once per interval per tenant.

        The SQL read runs without STATE_LOCK so a slow or waking database never
        blocks other requests. A local add/remove during the read bumps the
        generation, and the stale read is then discarded in favour of the cache.
        """
        from sqlalchemy.exc import SQLAlchemyError
        from backend import database
        from backend.workspace_repository import WorkspaceRepository

        with STATE_LOCK:
            refreshed_at = self._tenant_refreshed_at.get(tenant_id)
            if refreshed_at is not None and self._clock() - refreshed_at < self.SOURCE_REFRESH_SECONDS:
                return self._tenant_cache(tenant_id)
            generation = self._source_generation
            known_guardrails = set(self.workspace_guardrails)
            known_history = set(self.query_history)

        try:
            with database.SessionLocal() as db:
                repository = WorkspaceRepository(db)
                workspaces = repository.list_for_tenant(tenant_id)
                db.commit()  # Saves a source path moved by a restore; no-op otherwise.
                guardrails = {
                    workspace["id"]: repository.get_guardrails(workspace["id"])
                    for workspace in workspaces if workspace["id"] not in known_guardrails
                }
                history = {
                    workspace["id"]: repository.get_history(workspace["id"])
                    for workspace in workspaces if workspace["id"] not in known_history
                }
        except SQLAlchemyError:
            logger.warning("shared_source_refresh_failed", exc_info=True)
            with STATE_LOCK:
                return self._tenant_cache(tenant_id)

        with STATE_LOCK:
            if generation != self._source_generation:
                # This process changed sources mid-read; its cache is newer than the read.
                return self._tenant_cache(tenant_id)
            current_ids = {workspace["id"] for workspace in workspaces}
            for workspace_id, cached in list(self.workspaces.items()):
                if cached.get("owner_user_id") == tenant_id and workspace_id not in current_ids:
                    self.workspaces.pop(workspace_id, None)
                    self.workspace_guardrails.pop(workspace_id, None)
                    self.query_history.pop(workspace_id, None)
                    self.schema_snapshots.pop(f"schema_{workspace_id}", None)
                    self.active_proposals.pop(workspace_id, None)
            for workspace in workspaces:
                workspace_id = workspace["id"]
                existing = self.workspaces.get(workspace_id, {})
                self.workspaces[workspace_id] = self._keep_runtime_credentials(workspace, existing)
                if workspace_id not in self.workspace_guardrails:
                    self.workspace_guardrails[workspace_id] = {
                        **self.DEFAULT_GUARDRAILS, **guardrails.get(workspace_id, {}),
                    }
                if workspace_id not in self.query_history:
                    self.query_history[workspace_id] = history.get(workspace_id, [])
            self._tenant_refreshed_at[tenant_id] = self._clock()
            return [dict(workspace) for workspace in workspaces]

    @serialized
    def get_schema(self, workspace_id):
        return self.schema_snapshots.get(f"schema_{workspace_id}")

    @serialized
    def set_schema(self, workspace_id, schema):
        if workspace_id in self.workspaces:
            self.schema_snapshots[f"schema_{workspace_id}"] = schema

    @serialized
    def get_history(self, workspace_id):
        return deepcopy(self.query_history.get(workspace_id, []))

    @staticmethod
    def _managed_file(workspace):
        """The upload file Puchoo.si owns for a cached source, or None."""
        from pathlib import Path
        from apps.core.workspaces import workspace_source_file
        if workspace.get("source_type") == "server":
            return None
        uri = workspace.get("database_uri")
        if not uri and workspace.get("source_ref"):
            uri = f"sqlite:///{Path(str(workspace['source_ref'])).expanduser()}"
        return workspace_source_file(uri)

    def _store_source_copy(self, db, workspace):
        from backend.source_blobs import store_source
        source = self._managed_file(workspace)
        tenant_id = workspace.get("owner_user_id")
        if source is None or not tenant_id:
            return None
        return store_source(db, workspace_id=str(workspace["id"]), tenant_id=str(tenant_id), database_file=source)

    def add_workspace(self, workspace):
        ws_id = workspace["id"]
        def action(db):
            self._store_source_copy(db, workspace)
        with self._edit(ws_id, action):
            if ws_id in self.workspaces:
                raise ValueError("Workspace already exists")
            self.workspaces[ws_id] = dict(workspace)
            self.workspace_guardrails[ws_id] = self.DEFAULT_GUARDRAILS.copy()
            self.query_history[ws_id] = []
            self._invalidate_tenant_refresh(workspace.get("owner_user_id"))

    def append_history(self, workspace_id, record):
        from backend import database
        from backend.memory_deletions import generation, suppressed
        with self._edit(workspace_id):
            workspace = self.workspaces.get(workspace_id)
            if workspace is None:
                raise ValueError("Workspace was removed")
            tenant = workspace.get("owner_user_id")
            record = deepcopy(record)
            with database.SessionLocal() as db:
                record["memory_generation"] = generation(db, tenant, workspace_id)
                if suppressed(db, tenant, workspace_id, str(record.get("id")), record["memory_generation"]):
                    raise ValueError("History record was deleted")
            self.query_history.setdefault(workspace_id, []).insert(0, record)
        return record

    def set_history_presentation(self, tenant_id, workspace_id, original, presentation):
        """Patch only the same surviving record, never recreate deleted history.

        Translation happens outside this serialized commit. Generation and claim
        identity prevent a late result patch from touching a reused record ID.
        """
        with self._edit(workspace_id):
            workspace = self.workspaces.get(workspace_id)
            if not workspace or workspace.get("owner_user_id") != tenant_id:
                return False
            for record in self.query_history.get(workspace_id, []):
                if (record.get("id") == original.get("id")
                        and record.get("memory_generation") == original.get("memory_generation")
                        and record.get("approved_pending_id") == original.get("approved_pending_id")):
                    record["presentation"] = deepcopy(presentation)
                    return True
        return False

    def set_guardrails(self, workspace_id, guardrails):
        with self._edit(workspace_id):
            if workspace_id not in self.workspaces:
                raise ValueError("Workspace was removed")
            self.workspace_guardrails[workspace_id] = dict(guardrails)

    def delete_history(self, tenant_id, workspace_id, record_id=None):
        from backend.memory_deletions import enqueue
        receipt = []
        def action(db):
            receipt.append(enqueue(db, tenant_id, workspace_id, record_id))
        with self._edit(workspace_id, action):
            workspace = self.workspaces.get(workspace_id)
            if not workspace or workspace.get("owner_user_id") != tenant_id:
                raise ValueError("Workspace not found")
            records = self.query_history.get(workspace_id, [])
            self.query_history[workspace_id] = [] if record_id is None else [row for row in records if str(row.get("id")) != record_id]
            self.active_proposals.pop(workspace_id, None)
        return receipt[0]

    def remove_workspace(self, workspace_id):
        from sqlalchemy import delete
        from backend.models import Notification, PendingQuery, WorkspaceRecord
        from backend.memory_deletions import enqueue
        from backend.source_blobs import delete_source
        removed = None
        def action(db):
            record = db.get(WorkspaceRecord, workspace_id)
            if record is not None:
                enqueue(db, record.tenant_id, workspace_id)
                # Pending proposals keep the question and SQL literals, and
                # notifications name the source; neither outlives the data.
                db.execute(delete(PendingQuery).where(
                    PendingQuery.workspace_id == workspace_id, PendingQuery.tenant_id == record.tenant_id))
                db.execute(delete(Notification).where(Notification.resource_id == workspace_id))
                delete_source(db, workspace_id)
                db.delete(record)
        with self._edit(db_action=action):
            removed = self.workspaces.pop(workspace_id, None)
            self._invalidate_tenant_refresh(removed.get("owner_user_id") if removed else None)
            self.workspace_guardrails.pop(workspace_id, None)
            self.query_history.pop(workspace_id, None)
            self.schema_snapshots.pop(f"schema_{workspace_id}", None)
            self.active_proposals.pop(workspace_id, None)
        uri = removed.get("database_uri") if isinstance(removed, dict) else None
        try:
            from apps.api.routers.query import clear_translation_cache
            clear_translation_cache()
        except ImportError:
            pass
        with STATE_LOCK:
            if uri and not any(ws.get("database_uri") == uri for ws in self.workspaces.values()):
                from apps.core.workspaces import delete_workspace_files
                delete_workspace_files(uri)

    def persist_source(self, workspace_id, tenant_id):
        """Refresh the durable copy after a source file changed in place (Tally append)."""
        from backend import database
        workspace = self.get_workspace(workspace_id)
        if not workspace or workspace.get("owner_user_id") != tenant_id:
            return None
        with database.SessionLocal() as db:
            result = self._store_source_copy(db, workspace)
            db.commit()
        return result

    def source_changed(self, workspace_id, tenant_id, *, drop_pending=False):
        """A file was added to, removed from or replaced in a company.

        Refreshes the durable copy in the same commit, then drops everything
        derived from the old tables: the schema cache, any open proposal and
        cached translations. ``drop_pending`` also removes stored proposals,
        which can hold literals from data that was just removed.
        Raises if the copy could not be saved so the caller can undo the file.
        """
        from sqlalchemy import delete
        from backend.models import PendingQuery

        workspace = self.get_workspace(workspace_id)
        if not workspace or workspace.get("owner_user_id") != tenant_id:
            raise ValueError("Workspace not found")
        stored = {}

        def action(db):
            result = self._store_source_copy(db, workspace)
            stored["durable"] = bool(result and result.stored)
            if drop_pending:
                db.execute(delete(PendingQuery).where(
                    PendingQuery.workspace_id == workspace_id, PendingQuery.tenant_id == tenant_id))

        with self._edit(workspace_id, action):
            if workspace_id not in self.workspaces:
                raise ValueError("Workspace was removed")
            self.schema_snapshots.pop(f"schema_{workspace_id}", None)
            self.active_proposals.pop(workspace_id, None)
            self._invalidate_tenant_refresh(tenant_id)
        try:
            from apps.api.routers.query import clear_translation_cache
            clear_translation_cache()
        except ImportError:
            pass
        return stored.get("durable", False)

    def absorb_source(self, target_id, source_id, tenant_id, *, source_name=None):
        """Finish moving one data source into a company, in one database commit.

        The target's file already holds the moved tables. This saves its
        durable copy, moves the source's question history over, and removes
        the source the same way a delete does (pending proposals, notices,
        stored copy, memory). The source's own file is deleted after commit.
        """
        from sqlalchemy import delete
        from backend import database
        from backend.models import Notification, PendingQuery, WorkspaceRecord
        from backend.memory_deletions import enqueue, generation
        from backend.source_blobs import delete_source

        target = self.get_workspace(target_id)
        source = self.get_workspace(source_id)
        if (not target or not source or target_id == source_id
                or target.get("owner_user_id") != tenant_id or source.get("owner_user_id") != tenant_id):
            raise ValueError("Workspace not found")
        with database.SessionLocal() as db:
            target_generation = generation(db, tenant_id, target_id)

        def action(db):
            self._store_source_copy(db, self.workspaces[target_id])
            record = db.get(WorkspaceRecord, source_id)
            if record is None or record.tenant_id != tenant_id:
                raise ValueError("Workspace not found")
            enqueue(db, tenant_id, source_id)
            db.execute(delete(PendingQuery).where(
                PendingQuery.workspace_id == source_id, PendingQuery.tenant_id == tenant_id))
            db.execute(delete(Notification).where(Notification.resource_id == source_id))
            delete_source(db, source_id)
            db.delete(record)

        moved = 0
        with self._edit(target_id, action):
            if target_id not in self.workspaces or source_id not in self.workspaces:
                raise ValueError("Workspace was removed")
            existing_ids = {str(item.get("id")) for item in self.query_history.get(target_id, [])}
            carried = []
            for item in self.query_history.get(source_id, []):
                if str(item.get("id")) in existing_ids:
                    continue
                entry = deepcopy(item)
                entry["memory_generation"] = target_generation
                entry["moved_from"] = source_name or source.get("name")
                carried.append(entry)
            combined = self.query_history.get(target_id, []) + carried
            combined.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            self.query_history[target_id] = combined
            moved = len(carried)
            removed = self.workspaces.pop(source_id, None)
            self.workspace_guardrails.pop(source_id, None)
            self.query_history.pop(source_id, None)
            for workspace_id in (source_id, target_id):
                self.schema_snapshots.pop(f"schema_{workspace_id}", None)
                self.active_proposals.pop(workspace_id, None)
            self._invalidate_tenant_refresh(tenant_id)
        try:
            from apps.api.routers.query import clear_translation_cache
            clear_translation_cache()
        except ImportError:
            pass
        uri = removed.get("database_uri") if isinstance(removed, dict) else None
        if not uri and isinstance(removed, dict) and removed.get("source_ref"):
            uri = f"sqlite:///{removed['source_ref']}"
        with STATE_LOCK:
            if uri and not any(ws.get("database_uri") == uri for ws in self.workspaces.values()):
                from apps.core.workspaces import delete_workspace_files
                delete_workspace_files(uri)
        return moved

    def ensure_source_on_disk(self, workspace_id, tenant_id):
        """Bring back a wiped upload from its database copy before it is used.

        Returns the (possibly refreshed) cached workspace. Only the caller's own
        tenant row is read, so another tenant's copy can never be restored.
        """
        from pathlib import Path
        from sqlalchemy import select
        from backend import database
        from backend.models import WorkspaceRecord
        from sqlalchemy.exc import SQLAlchemyError
        from backend.source_blobs import restore_source
        from backend.workspace_repository import WorkspaceRepository

        workspace = self.get_workspace(workspace_id)
        if not workspace or workspace.get("owner_user_id") != tenant_id or workspace.get("source_type") == "server":
            return workspace
        source_ref = workspace.get("source_ref") or str(workspace.get("database_uri") or "").removeprefix("sqlite:///")
        if workspace.get("availability") != "unavailable" and source_ref and Path(source_ref).expanduser().is_file():
            return workspace
        try:
            with database.SessionLocal() as db:
                record = db.scalar(select(WorkspaceRecord).where(
                    WorkspaceRecord.id == workspace_id, WorkspaceRecord.tenant_id == tenant_id))
                if record is None:
                    return workspace
                if restore_source(db, record) is None:
                    return self._mark_unavailable(workspace_id, tenant_id) or workspace
                db.commit()
                restored = WorkspaceRepository._as_dict(record)
        except (SQLAlchemyError, OSError):
            logger.warning("source_restore_lookup_failed", exc_info=True)
            return workspace
        with STATE_LOCK:
            cached = self.workspaces.get(workspace_id)
            if cached is not None and cached.get("owner_user_id") == tenant_id:
                for key in ("source_ref", "database_uri", "availability"):
                    if key in restored:
                        cached[key] = restored[key]
            return dict(cached) if cached is not None else None

    def _mark_unavailable(self, workspace_id, tenant_id):
        """The file is gone and there is no copy: say so instead of opening an empty file."""
        with STATE_LOCK:
            cached = self.workspaces.get(workspace_id)
            if cached is None or cached.get("owner_user_id") != tenant_id:
                return None
            cached["availability"] = "unavailable"
            cached.pop("database_uri", None)
            return dict(cached)

    def replace_source(self, workspace_id, tenant_id, replacement):
        """Point an existing source at a newly uploaded file, keeping its id.

        Name, guardrails and history stay; the schema cache and any open
        proposal are dropped because the tables may have changed.
        """
        from backend.workspace_repository import SAFE_METADATA_KEYS
        new_uri = replacement["database_uri"]
        new_path = new_uri.removeprefix("sqlite:///")
        previous = {}

        def action(db):
            stored = self._store_source_copy(db, self.workspaces[workspace_id])
            previous["stored"] = bool(stored and stored.stored)

        with self._edit(workspace_id, action):
            workspace = self.workspaces.get(workspace_id)
            if not workspace or workspace.get("owner_user_id") != tenant_id:
                raise ValueError("Workspace not found")
            if workspace.get("source_type") == "server":
                raise ValueError("Server connections are reconnected, not re-uploaded")
            previous["file"] = self._managed_file(workspace)
            updated = {key: value for key, value in workspace.items()
                       if key not in SAFE_METADATA_KEYS and key != "connect_args"}
            updated.update(
                database_uri=new_uri,
                source_ref=new_path,
                dialect=replacement.get("dialect", "sqlite"),
                source_type=replacement.get("source_type", workspace.get("source_type")),
                availability="available",
            )
            self.workspaces[workspace_id] = updated
            self.schema_snapshots.pop(f"schema_{workspace_id}", None)
            self.active_proposals.pop(workspace_id, None)
            self._invalidate_tenant_refresh(tenant_id)
        try:
            from apps.api.routers.query import clear_translation_cache
            clear_translation_cache()
        except ImportError:
            pass
        old_file = previous.get("file")
        if old_file is not None and str(old_file) != str(new_path):
            with STATE_LOCK:
                old_uri = f"sqlite:///{old_file}"
                if not any(ws.get("database_uri") == old_uri for ws in self.workspaces.values()):
                    from apps.core.workspaces import delete_workspace_files
                    delete_workspace_files(old_uri)
        return {**self.get_workspace(workspace_id), "durable": previous.get("stored", False)}

    @serialized
    def remove_organization(self, tenant_id):
        """Commit account/source deletion together, then discard derived local state."""
        from sqlalchemy import delete, func, select
        from backend import database
        from backend.models import User, WorkspaceRecord
        from backend.memory_deletions import enqueue
        from backend.source_blobs import delete_tenant_sources
        from apps.core.workspaces import delete_workspace_files

        uris = set()
        with database.SessionLocal() as db:
            records = db.scalars(select(WorkspaceRecord).where(WorkspaceRecord.tenant_id == tenant_id)).all()
            for record in records:
                enqueue(db, tenant_id, record.id)
                if record.source_type != "server" and record.source_ref:
                    uris.add(f"sqlite:///{record.source_ref}")
            # Stored file copies go with the organization (the FK cascade does too).
            delete_tenant_sources(db, tenant_id)
            # Match the canonical tenant policy, including legacy institution members.
            removed_accounts = db.execute(delete(User).where(func.coalesce(
                User.workspace_owner_id, User.institute_owner_id, User.id,
            ) == tenant_id)).rowcount or 0
            db.commit()
        for workspace_id, workspace in list(self.workspaces.items()):
            if workspace.get("owner_user_id") != tenant_id:
                continue
            if workspace.get("source_type") != "server" and workspace.get("database_uri"):
                uris.add(workspace["database_uri"])
            self.workspaces.pop(workspace_id, None)
            self.workspace_guardrails.pop(workspace_id, None)
            self.query_history.pop(workspace_id, None)
            self.schema_snapshots.pop(f"schema_{workspace_id}", None)
            self.active_proposals.pop(workspace_id, None)
        self._invalidate_tenant_refresh(tenant_id)
        self.save()
        try:
            from apps.api.routers.query import clear_translation_cache
            clear_translation_cache()
        except ImportError:
            pass
        for uri in uris:
            if not any(ws.get("database_uri") == uri for ws in self.workspaces.values()):
                delete_workspace_files(uri)
        return removed_accounts

    @serialized
    def save(self, active_workspace_id=None, db_action=None):
        """SQL commits before the derived, best-effort JSON checkpoint."""
        if active_workspace_id is not None or db_action is not None:
            self._save_to_database([active_workspace_id] if active_workspace_id else [], db_action=db_action)
        try:
            save_local_state(workspaces=self.workspaces, active_workspace_id=active_workspace_id,
                workspace_guardrails=self.workspace_guardrails, query_history=self.query_history, profile=self.profile)
        except OSError:
            # SQL is durable. Stale JSON must never be replayed over it at startup.
            logger.warning("local_state_checkpoint_failed", exc_info=True)

    @serialized
    def hydrate_from_database(self):
        from sqlalchemy import select
        from backend import database
        from backend.models import WorkspaceRecord, Tenant, PersistenceMarker
        from backend.workspace_repository import WorkspaceRepository, import_legacy_local_state
        from backend.source_blobs import has_source
        with database.SessionLocal() as db:
            if db.get(PersistenceMarker, "relational-authority-v1") is None:
                # Any existing tenant state is authoritative, including zero remaining workspaces.
                if db.scalar(select(Tenant.id).limit(1)) is None and db.scalar(select(WorkspaceRecord.id).limit(1)) is None:
                    import_legacy_local_state(db)
                db.add(PersistenceMarker(key="relational-authority-v1"))
                db.commit()
            repository = WorkspaceRepository(db)
            runtime = self.workspaces
            workspaces, guardrails, history = {}, {}, {}
            for record in db.scalars(select(WorkspaceRecord)).all():
                # Restores an upload wiped by a restart from its database copy.
                workspace = repository.resolve(record)
                if workspace.get("availability") == "available" and not has_source(
                        db, workspace_id=record.id, tenant_id=record.tenant_id):
                    # Sources uploaded before durable copies existed get one now.
                    self._store_source_copy(db, workspace)
                workspaces[record.id] = self._keep_runtime_credentials(workspace, runtime.get(record.id, {}))
                guardrails[record.id] = {**self.DEFAULT_GUARDRAILS, **repository.get_guardrails(record.id)}
                history[record.id] = repository.get_history(record.id)
            db.commit()
            self.workspaces, self.workspace_guardrails, self.query_history = workspaces, guardrails, history
            self._invalidate_tenant_refresh()

    def _save_to_database(self, workspace_ids: Iterable[str] | None = None, db_action=None) -> None:
        from backend import database
        from backend.models import Tenant, User
        from backend.workspace_repository import WorkspaceRepository

        ids = list(self.workspaces) if workspace_ids is None else list(workspace_ids)
        with database.SessionLocal() as db:
            repository = WorkspaceRepository(db)
            for workspace_id in ids:
                workspace = self.workspaces.get(workspace_id)
                if not workspace:
                    continue
                tenant_id = workspace.get("owner_user_id")
                if not tenant_id:
                    raise ValueError("Workspace requires a tenant owner")
                if db.get(Tenant, tenant_id) is None:
                    owner = db.get(User, tenant_id)
                    if owner is None:
                        raise ValueError("Workspace owner no longer exists")
                    repository.ensure_tenant(
                        tenant_id,
                        owner.id,
                        name=owner.workspace_name or owner.institute_name or "Workspace",
                        workspace_type=owner.workspace_type,
                    )
                repository.save_workspace(workspace, tenant_id)
                repository.save_guardrails(workspace_id, self.get_guardrails(workspace_id))
                repository.save_history(workspace_id, self.query_history.get(workspace_id, []))
            if db_action is not None:
                db_action(db)
            db.commit()

    @serialized
    def get_guardrails(self, workspace_id: str) -> Dict[str, Any]:
        """Return a complete guardrail configuration, including for legacy state."""

        return {
            **self.DEFAULT_GUARDRAILS,
            **self.workspace_guardrails.get(workspace_id, {}),
        }

# One singleton/owner per API process; run one API worker.
session_manager = SessionManager()
