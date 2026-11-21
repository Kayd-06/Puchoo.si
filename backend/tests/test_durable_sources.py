"""Uploaded sources survive a wiped disk, and wiped ones can be re-uploaded in place.

Render's free plan wipes the local disk on every restart. These tests delete the
local SQLite file to simulate that. All data is synthetic.
"""

from __future__ import annotations

import logging
import zlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.session import session_manager
from backend import database
from backend.main import create_app
from backend.models import WorkspaceHistoryRecord, WorkspaceRecord, WorkspaceSourceBlob
from backend.source_blobs import managed_source_path, restore_source, store_source
from backend.tests.conftest import csrf_headers, finish_signup, signup_payload
from backend.tests.test_tally_upload import _ask, _confirm, _files, client  # noqa: F401  (client is a fixture)
from tests.fixtures.tally import generate as fx

CSV = b"region,amount\nNorth,120\nSouth,80\nZanzibarqx,5\n"


def _upload(test_client, contents=CSV, filename="sales.csv", name="Regional sales"):
    response = test_client.post(
        "/api/workspaces/upload", data={"name": name}, files=_files(filename, contents), headers=csrf_headers(test_client)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _source_file(workspace_id: str) -> Path:
    workspace = session_manager.workspaces[workspace_id]
    return Path(workspace.get("source_ref") or workspace["database_uri"].removeprefix("sqlite:///"))


def _wipe(path: Path) -> None:
    """What a Render restart does to an upload."""
    for candidate in path.parent.iterdir():
        if candidate.name.startswith(path.name):
            candidate.unlink()
    assert not path.exists()


def _blob(workspace_id: str):
    with database.SessionLocal() as db:
        return db.get(WorkspaceSourceBlob, workspace_id)


def _restart() -> None:
    """A new process: only the database survives; the cache is rebuilt from it."""
    session_manager.workspaces = {}
    session_manager.workspace_guardrails = {}
    session_manager.query_history = {}
    session_manager.schema_snapshots = {}
    session_manager.active_proposals = {}
    session_manager.hydrate_from_database()


def test_upload_keeps_a_compressed_copy_in_the_database(client):
    workspace = _upload(client)
    blob = _blob(workspace["id"])
    assert blob is not None
    raw = zlib.decompress(blob.content)
    assert raw.startswith(b"SQLite format 3\x00")
    assert blob.size_bytes == len(raw)
    assert len(blob.sha256) == 64
    with database.SessionLocal() as db:
        assert blob.tenant_id == db.get(WorkspaceRecord, workspace["id"]).tenant_id


def test_question_still_works_after_the_file_is_wiped(client):
    body = _confirm(client, "Sales Register.xlsx", fx.sales_register_xlsx()).json()
    workspace_id = body["workspace"]["id"]
    source = _source_file(workspace_id)
    _wipe(source)

    rows = _ask(client, workspace_id, "total sales in November")

    assert rows == [{"total_sales": pytest.approx(float(fx.NOVEMBER_TOTAL))}]
    assert source.is_file()
    # The Tally metadata sidecar comes back too, so the source is still a Tally report.
    assert Path(str(source) + ".report.json").is_file()


def test_restart_restores_wiped_files_before_serving(client):
    workspace = _upload(client)
    source = _source_file(workspace["id"])
    _wipe(source)

    _restart()

    assert source.is_file()
    listed = {item["id"]: item for item in client.get("/api/workspaces/").json()}
    assert listed[workspace["id"]]["availability"] == "available"
    schema = client.get(f"/api/workspaces/{workspace['id']}/schema")
    assert schema.status_code == 200 and "Table: sales" in schema.json()["schema"]


def test_appended_tally_months_survive_a_wipe(client):
    workspace_id = _confirm(client, "Sales Register.xlsx", fx.sales_register_xlsx()).json()["workspace"]["id"]
    december = fx.sales_register_xlsx(fx.DECEMBER_VOUCHERS, "1-Dec-26 to 31-Dec-26")
    assert _confirm(client, "Dec.xlsx", december, target_workspace_id=workspace_id).status_code == 200
    _wipe(_source_file(workspace_id))

    expected = float(fx.NOVEMBER_TOTAL) + sum(float(amount) for *_, amount in fx.DECEMBER_VOUCHERS)
    assert _ask(client, workspace_id, "total sales") == [{"total_sales": pytest.approx(expected)}]


def test_sources_uploaded_before_this_change_get_a_copy_at_startup(client, tmp_path):
    workspace = _upload(client)
    with database.SessionLocal() as db:
        db.query(WorkspaceSourceBlob).delete()
        db.commit()

    _restart()

    assert _blob(workspace["id"]) is not None


def test_copy_is_only_restored_for_its_own_tenant(client, tmp_path):
    workspace = _upload(client)
    source = _source_file(workspace["id"])
    _wipe(source)
    with database.SessionLocal() as db:
        real = db.get(WorkspaceRecord, workspace["id"])
        imposter = WorkspaceRecord(id=real.id, tenant_id="another-tenant", name="x",
                                   source_type="spreadsheet", source_ref=real.source_ref)
        assert restore_source(db, imposter) is None
    assert not source.exists()


def test_other_tenant_cannot_reach_or_reupload_a_source(client, otp_codes):
    workspace = _upload(client)
    with TestClient(create_app(include_product=True)) as other:
        email = f"other-{uuid4().hex}@elsewhere.com"
        payload = signup_payload(email=email, workspace_type="business", workspace_name="Elsewhere")
        assert other.post("/api/v1/auth/signup", json=payload, headers=csrf_headers(other)).status_code == 202
        finish_signup(other, email, otp_codes)
        _wipe(_source_file(workspace["id"]))  # After app startup, which restores every source.
        assert other.get(f"/api/workspaces/{workspace['id']}/schema").status_code == 404
        assert not _source_file(workspace["id"]).exists()
        replaced = other.post(f"/api/workspaces/{workspace['id']}/reupload", files=_files("x.csv", b"a,b\n1,2\n"),
                              headers=csrf_headers(other))
        assert replaced.status_code == 404
        assert workspace["id"] not in {item["id"] for item in other.get("/api/workspaces/").json()}


def test_deleting_a_source_removes_its_copy(client):
    workspace = _upload(client)
    assert _blob(workspace["id"]) is not None
    assert client.delete(f"/api/workspaces/{workspace['id']}", headers=csrf_headers(client)).status_code == 204
    assert _blob(workspace["id"]) is None


def test_deleting_the_organization_removes_every_copy(client):
    first, second = _upload(client), _upload(client, name="Second")
    response = client.request("DELETE", "/api/v1/auth/workspace",
                              json={"confirmation": "Demo Traders", "current_password": "language10"},
                              headers=csrf_headers(client))
    assert response.status_code == 204, response.text
    with database.SessionLocal() as db:
        assert db.query(WorkspaceSourceBlob).filter(
            WorkspaceSourceBlob.workspace_id.in_([first["id"], second["id"]])).count() == 0


def test_wiped_source_without_a_copy_is_flagged_and_can_be_reuploaded_in_place(client):
    workspace = _upload(client)
    workspace_id = workspace["id"]
    client.fake.generate_sql = lambda **_kwargs: "SELECT SUM(amount) AS total_amount FROM sales"
    assert _ask(client, workspace_id, "total amount") == [{"total_amount": 205}]  # One history entry to keep.
    guardrails = {"max_rows": 42, "timeout_seconds": 12, "confirm_complex_queries": False}
    assert client.put(f"/api/settings/guardrails/{workspace_id}", json=guardrails, headers=csrf_headers(client)).status_code == 200
    old_source = _source_file(workspace_id)
    # A source wiped before durable copies existed: no file, no copy.
    with database.SessionLocal() as db:
        db.query(WorkspaceSourceBlob).delete()
        db.commit()
    _wipe(old_source)
    _restart()

    listed = {item["id"]: item for item in client.get("/api/workspaces/").json()}
    assert listed[workspace_id]["availability"] == "unavailable"
    blocked = client.post(f"/api/query/{workspace_id}/generate", json={"question": "total"}, headers=csrf_headers(client))
    assert blocked.status_code == 409
    assert "uploaded again" in blocked.json()["detail"]

    count_before = len(client.get("/api/workspaces/").json())
    replaced = client.post(f"/api/workspaces/{workspace_id}/reupload",
                           files=_files("sales.csv", CSV), headers=csrf_headers(client))

    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["id"] == workspace_id
    assert replaced.json()["name"] == "Regional sales"
    assert replaced.json()["availability"] == "available"
    assert len(client.get("/api/workspaces/").json()) == count_before
    assert len(client.get(f"/api/history/{workspace_id}").json()) == 1
    assert client.get(f"/api/settings/guardrails/{workspace_id}").json() == guardrails
    assert _blob(workspace_id) is not None
    with database.SessionLocal() as db:
        assert db.query(WorkspaceHistoryRecord).filter_by(workspace_id=workspace_id).count() == 1
    assert _ask(client, workspace_id, "total amount") == [{"total_amount": 205}]


def test_reupload_replaces_a_working_file_and_removes_the_old_one(client):
    workspace = _upload(client)
    old_source = _source_file(workspace["id"])
    replaced = client.post(f"/api/workspaces/{workspace['id']}/reupload",
                           files=_files("sales.csv", b"region,amount\nEast,7\n"), headers=csrf_headers(client))
    assert replaced.status_code == 200, replaced.text
    assert not old_source.exists()
    assert _source_file(workspace["id"]).is_file()


def test_tally_source_must_be_reuploaded_with_a_tally_report(client):
    workspace_id = _confirm(client, "Sales Register.xlsx", fx.sales_register_xlsx()).json()["workspace"]["id"]
    wrong = client.post(f"/api/workspaces/{workspace_id}/reupload", files=_files("plain.csv", b"a,b\n1,2\n"),
                        headers=csrf_headers(client))
    assert wrong.status_code == 400 and "Tally" in wrong.json()["detail"]
    right = client.post(f"/api/workspaces/{workspace_id}/reupload",
                        files=_files("Sales Register.xlsx", fx.sales_register_xlsx()), headers=csrf_headers(client))
    assert right.status_code == 200, right.text
    assert right.json()["source_type"] == "tally_report"


def test_viewer_cannot_reupload(client, otp_codes):
    workspace = _upload(client)
    code = client.post("/api/v1/auth/workspace/invite", json={"role": "viewer"}, headers=csrf_headers(client)).json()["code"]
    with TestClient(create_app(include_product=True)) as viewer:
        email = f"viewer-{uuid4().hex}@tallytest.com"
        viewer.post("/api/v1/auth/signup", json=signup_payload(email=email, invite_code=code), headers=csrf_headers(viewer))
        finish_signup(viewer, email, otp_codes)
        denied = viewer.post(f"/api/workspaces/{workspace['id']}/reupload", files=_files("s.csv", CSV),
                             headers=csrf_headers(viewer))
        assert denied.status_code == 403


def test_reupload_uses_the_upload_size_limit(client, monkeypatch):
    from apps.api.routers import workspaces as router
    workspace = _upload(client)
    monkeypatch.setattr(router, "MAX_UPLOAD_BYTES", 10)
    too_big = client.post(f"/api/workspaces/{workspace['id']}/reupload", files=_files("s.csv", CSV),
                          headers=csrf_headers(client))
    assert too_big.status_code == 413


def test_oversized_copy_is_skipped_but_upload_still_works(client, monkeypatch):
    monkeypatch.setenv("PUCHOO_SOURCE_BLOB_MAX_BYTES", "10")
    workspace = _upload(client)
    assert _blob(workspace["id"]) is None
    assert client.get(f"/api/workspaces/{workspace['id']}/schema").status_code == 200


def test_file_contents_are_never_logged(client, caplog):
    caplog.set_level(logging.DEBUG)
    workspace = _upload(client)
    _wipe(_source_file(workspace["id"]))
    _restart()
    assert "Zanzibarqx" not in caplog.text
    assert any("source_blob_restored" in record.getMessage() for record in caplog.records)


def test_restore_never_writes_outside_the_upload_directory(tmp_path, monkeypatch):
    from apps.core import workspaces
    root = tmp_path / "uploads"
    monkeypatch.setattr(workspaces, "LOCAL_WORKSPACE_DIRECTORY", root)
    assert managed_source_path(str(tmp_path / "elsewhere" / "pucho_import_a.db"), "ws_1") == root / "pucho_import_a.db"
    assert managed_source_path("/etc/passwd", "ws_1") == root / "pucho_restored_ws_1.db"
    assert managed_source_path(str(root / "pucho_x.db"), "ws_1") == root / "pucho_x.db"
    escaped = managed_source_path(None, "../../evil")
    assert escaped.parent == root and ".." not in escaped.name


def test_concurrent_restores_write_one_complete_file(client):
    from concurrent.futures import ThreadPoolExecutor
    workspace = _upload(client)
    source = _source_file(workspace["id"])
    _wipe(source)

    def restore(_):
        with database.SessionLocal() as db:
            return restore_source(db, db.get(WorkspaceRecord, workspace["id"]))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(restore, range(8)))
    assert all(result == source for result in results)
    assert source.read_bytes().startswith(b"SQLite format 3\x00")
    assert not [p for p in source.parent.iterdir() if p.name.startswith(".restore_")]


def test_store_refuses_to_overwrite_another_tenants_copy(client):
    workspace = _upload(client)
    with database.SessionLocal() as db:
        with pytest.raises(ValueError):
            store_source(db, workspace_id=workspace["id"], tenant_id="another-tenant",
                         database_file=_source_file(workspace["id"]))


@pytest.fixture(params=["sqlite", "postgresql"])
def any_database(request, tmp_path, monkeypatch):
    """The copy must round-trip on SQLite (local/tests) and Postgres (Neon)."""
    import os
    from sqlalchemy.engine import make_url
    from sqlalchemy.schema import CreateSchema, DropSchema

    from apps.core import workspaces

    original_url = database.engine.url.render_as_string(hide_password=False)
    schema = base = None
    if request.param == "postgresql":
        url = os.getenv("TEST_PERSISTENCE_POSTGRES_URL")
        if not url:
            pytest.skip("TEST_PERSISTENCE_POSTGRES_URL not configured (dedicated disposable DB)")
        base = database.make_engine(url)
        schema = "durable_test_" + uuid4().hex
        with base.begin() as connection:
            connection.execute(CreateSchema(schema))
        url = make_url(url).update_query_dict({"options": "-csearch_path=" + schema}).render_as_string(hide_password=False)
    else:
        url = "sqlite:///" + str(tmp_path / "durable.db")
    database.configure_database(url)
    database.init_db()
    monkeypatch.setattr(workspaces, "LOCAL_WORKSPACE_DIRECTORY", tmp_path / "uploads")
    try:
        yield request.param
    finally:
        database.configure_database(original_url)
        if base is not None:
            with base.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            base.dispose()


def test_store_restore_and_cascade_on_both_databases(any_database, tmp_path):
    import sqlite3
    from backend.models import User
    from backend.workspace_repository import WorkspaceRepository

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source = uploads / "pucho_import_demo.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sales (amount INTEGER)")
        connection.executemany("INSERT INTO sales VALUES (?)", [(n,) for n in range(500)])
    with database.SessionLocal() as db:
        for owner in ("owner-a", "owner-b"):
            db.add(User(id=owner, full_name="Synthetic", email=owner + "@example.com", password_hash="synthetic",
                        workspace_type="business", workspace_role="owner"))
        db.commit()
        repository = WorkspaceRepository(db)
        for owner in ("owner-a", "owner-b"):
            repository.ensure_tenant(owner, owner, name="Synthetic", workspace_type="business")
        repository.save_workspace({"id": "ws-a", "name": "Sales", "source_type": "spreadsheet",
                                   "source_ref": str(source)}, "owner-a")
        assert store_source(db, workspace_id="ws-a", tenant_id="owner-a", database_file=source).stored
        db.commit()

    source.unlink()
    with database.SessionLocal() as db:
        assert WorkspaceRepository(db).get_for_tenant("ws-a", "owner-b") is None
        assert not source.exists()
        workspace = WorkspaceRepository(db).get_for_tenant("ws-a", "owner-a")
        db.commit()
    assert workspace["availability"] == "available"
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sales").fetchone() == (500,)

    with database.SessionLocal() as db:
        db.delete(db.get(WorkspaceRecord, "ws-a"))
        db.commit()
        assert db.get(WorkspaceSourceBlob, "ws-a") is None
