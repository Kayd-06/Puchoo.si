"""What leaves the server and what is logged: schema only, no business values.

All data here is synthetic (Demo Traders Pvt Ltd, fake people and amounts).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api.routers import query as query_router
from apps.api.session import session_manager
from apps.core import data_privacy
from apps.core import workspaces as workspace_core
from apps.core.result_presentation import build_result_presentation
from apps.core.verification import DeepSeekVerifier
from apps.core.workspaces import get_schema_snapshot, workspace_source_file
from backend import database
from backend.main import create_app
from backend.models import MemoryDeletion, Notification, PendingQuery
from backend.tests.conftest import csrf_headers, finish_signup, signup_payload

PARTY = "Sharma Hardware Stores"
EMAIL = "accounts@demotraders.example"
PHONE = "98765 43210"


class CapturingChat:
    def __init__(self) -> None:
        self.users: list[str] = []

    def _chat(self, *, system, user, **_kwargs):
        self.users.append(user)
        return json.dumps({"verdict": "MATCH", "confidence": 90, "summary": "ok", "details": "ok"})


def _verify(monkeypatch, share: str | None):
    if share is None:
        monkeypatch.delenv(data_privacy.SHARE_RESULT_ROWS_ENV, raising=False)
    else:
        monkeypatch.setenv(data_privacy.SHARE_RESULT_ROWS_ENV, share)
    chat = CapturingChat()
    result = DeepSeekVerifier(client=chat, model="test").verify(
        question="Total sales per party",
        sql="SELECT party, SUM(debit_amount) AS total FROM sales_register GROUP BY party",
        columns=["party", "total"],
        rows=[{"party": PARTY, "total": 123456.75}],
        row_count=1,
    )
    return chat.users[0], result


def test_verifier_receives_column_shape_but_no_rows_by_default(monkeypatch):
    payload, result = _verify(monkeypatch, None)
    assert PARTY not in payload and "123456" not in payload
    body = json.loads(payload)["result"]
    assert body["rows_withheld"] is True
    assert body["columns"] == ["party", "total"]
    assert body["column_kinds"] == {"party": "text", "total": "number"}
    assert body["row_count"] == 1
    assert result.status.value == "VERIFIED"


def test_verifier_rows_are_an_explicit_operator_opt_in(monkeypatch):
    payload, _ = _verify(monkeypatch, "1")
    assert PARTY in payload


def _categorical_db(path: Path, values: list[str]) -> str:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sales (party TEXT, voucher_type TEXT, payment_method TEXT, amount REAL)")
    connection.executemany(
        "INSERT INTO sales VALUES (?, ?, ?, ?)",
        [(PARTY, "Sales", method, 100.0) for method in values],
    )
    connection.commit()
    connection.close()
    return f"sqlite:///{path}"


def test_schema_hints_skip_values_that_could_be_personal_or_financial(tmp_path, monkeypatch):
    monkeypatch.delenv(data_privacy.SCHEMA_VALUE_HINTS_ENV, raising=False)
    uri = _categorical_db(tmp_path / "s.db", ["Cash", EMAIL, PHONE])
    schema = get_schema_snapshot(uri)
    assert "Values for voucher_type: 'Sales'" in schema
    assert "payment_method:" not in schema.split("Columns:", 1)[1].split("\n", 1)[1]
    for secret in (PARTY, EMAIL, PHONE, "Cash"):
        assert secret not in schema


def test_schema_hints_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv(data_privacy.SCHEMA_VALUE_HINTS_ENV, "off")
    schema = get_schema_snapshot(_categorical_db(tmp_path / "s.db", ["Cash"]))
    assert "Values for" not in schema
    assert "voucher_type" in schema


def test_imported_financial_reports_never_get_value_hints(tmp_path, monkeypatch):
    monkeypatch.delenv(data_privacy.SCHEMA_VALUE_HINTS_ENV, raising=False)
    path = tmp_path / "report.db"
    uri = _categorical_db(path, ["Cash"])
    data_privacy.report_metadata_path(path).write_text("{}")
    assert "Values for" not in get_schema_snapshot(uri)


def test_safe_value_hint_rules():
    assert data_privacy.safe_value_hint("Paid")
    for unsafe in (EMAIL, PHONE, "Invoice 824", "x" * 41, ""):
        assert not data_privacy.safe_value_hint(unsafe)


def test_result_cells_are_not_sent_to_the_translator_by_default(monkeypatch):
    calls = []

    class Recorder:
        def translate_many(self, texts, *_args, **_kwargs):
            calls.append(texts)
            return {text: text for text in texts}

    monkeypatch.delenv(data_privacy.TRANSLATE_RESULT_VALUES_ENV, raising=False)
    monkeypatch.setattr(query_router, "SarvamClient", Recorder)
    rows = [{"party": PARTY, "amount": 5}]
    assert query_router._localize_rows(rows, "hi-IN") == rows
    assert calls == []


def test_single_value_headline_keeps_the_value_out_of_translation():
    presentation = build_result_presentation("total sales", ["total_sales"], [{"total_sales": 482500.0}])
    assert presentation.headline == "Total Sales: 482,500"
    assert presentation.headline_label == "Total Sales"
    assert presentation.headline_value == "482,500"


def test_log_records_are_scrubbed_before_any_handler(caplog):
    data_privacy.install_log_redaction()
    log = logging.getLogger("apps.privacy-test")
    with caplog.at_level(logging.INFO, logger="apps.privacy-test"):
        log.info("party=%s mail=%s phone=%s sql=%s", "x", EMAIL, PHONE, f"WHERE party = '{PARTY}'")
        detail = f"could not convert string to float: '{PARTY}' 123456789012"
        try:
            raise ValueError(detail)
        except ValueError:
            log.exception("import failed type=%s", "ValueError")
    text = caplog.text
    for secret in (EMAIL, PHONE, PARTY, "123456789012"):
        assert secret not in text
    assert "ValueError: <message redacted>" in text
    assert "import failed type=ValueError" in text


def test_scrub_keeps_dates_counts_and_ids_readable():
    line = "query ok rows=42 latency_ms=118 date=2026-11-19 workspace=ws_ab12cd34ef56"
    assert data_privacy.scrub_text(line) == line


def test_install_is_idempotent():
    data_privacy.install_log_redaction()
    factory = logging.getLogRecordFactory()
    data_privacy.install_log_redaction()
    assert logging.getLogRecordFactory() is factory


def _signed_in(client, otp_codes):
    email = f"owner-{uuid4().hex}@privacytest.com"
    payload = signup_payload(email=email, workspace_type="business", workspace_name="Demo Traders")
    response = client.post("/api/v1/auth/signup", json=payload, headers=csrf_headers(client))
    assert response.status_code == 202
    finish_signup(client, email, otp_codes)


def test_deleting_an_upload_removes_file_metadata_pending_queries_and_notifications(app, tmp_path, monkeypatch, otp_codes):
    monkeypatch.setattr(workspace_core, "LOCAL_WORKSPACE_DIRECTORY", tmp_path)
    monkeypatch.setattr("apps.api.session.save_local_state", lambda **kwargs: None)
    monkeypatch.setattr(query_router, "_translation_cache", {("hi-IN", PARTY): "translated"})
    with TestClient(create_app(include_product=True)) as client:
        _signed_in(client, otp_codes)
        uploaded = client.post(
            "/api/workspaces/upload",
            files={"file": ("sales.csv", f"party,amount\n{PARTY},100\n".encode(), "text/csv")},
            headers=csrf_headers(client),
        )
        assert uploaded.status_code == 200, uploaded.text
        workspace_id = uploaded.json()["id"]
        workspace = session_manager.workspaces[workspace_id]
        stored = workspace_source_file(workspace["database_uri"], root=tmp_path)
        sidecar = data_privacy.report_metadata_path(stored)
        sidecar.write_text(json.dumps({"company_name": "Demo Traders Pvt Ltd"}))
        with database.SessionLocal() as db:
            db.add(PendingQuery(
                id=f"pq_{uuid4().hex[:12]}", user_id=db.scalar(select(Notification.user_id).limit(1)),
                tenant_id=workspace["owner_user_id"], workspace_id=workspace_id,
                sql=f"SELECT * FROM sales WHERE party = '{PARTY}'", sql_hash="x" * 64,
                context_json=json.dumps({"question": f"sales for {PARTY}"}),
                expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            ))
            db.commit()
            assert db.scalar(select(Notification).where(Notification.resource_id == workspace_id)) is not None

        deleted = client.delete(f"/api/workspaces/{workspace_id}", headers=csrf_headers(client))
        assert deleted.status_code == 204

        assert not stored.exists() and not sidecar.exists()
        assert workspace_id not in session_manager.workspaces
        assert session_manager.query_history.get(workspace_id) is None
        assert query_router._translation_cache == {}
        with database.SessionLocal() as db:
            assert db.scalar(select(PendingQuery).where(PendingQuery.workspace_id == workspace_id)) is None
            assert db.scalar(select(Notification).where(Notification.resource_id == workspace_id)) is None
            assert db.scalar(select(MemoryDeletion).where(MemoryDeletion.workspace_id == workspace_id)) is not None
        assert client.get(f"/api/workspaces/{workspace_id}").status_code == 404


def test_viewers_cannot_delete_an_upload(app, tmp_path, monkeypatch, otp_codes):
    monkeypatch.setattr(workspace_core, "LOCAL_WORKSPACE_DIRECTORY", tmp_path)
    monkeypatch.setattr("apps.api.session.save_local_state", lambda **kwargs: None)
    with TestClient(create_app(include_product=True)) as owner:
        _signed_in(owner, otp_codes)
        workspace_id = owner.post(
            "/api/workspaces/upload",
            files={"file": ("sales.csv", b"party,amount\nA,1\n", "text/csv")},
            headers=csrf_headers(owner),
        ).json()["id"]
        code = owner.post("/api/v1/auth/workspace/invite", json={"role": "viewer"}, headers=csrf_headers(owner))
        assert code.status_code == 200, code.text
        with TestClient(create_app(include_product=True)) as viewer:
            email = f"viewer-{uuid4().hex}@privacytest.com"
            viewer.post(
                "/api/v1/auth/signup",
                json=signup_payload(email=email, workspace_type="personal", invite_code=code.json()["code"]),
                headers=csrf_headers(viewer),
            )
            finish_signup(viewer, email, otp_codes)
            assert viewer.delete(f"/api/workspaces/{workspace_id}", headers=csrf_headers(viewer)).status_code == 403
        assert workspace_id in session_manager.workspaces


def test_uploaded_spreadsheet_schema_for_the_model_has_no_cell_values(tmp_path, monkeypatch):
    monkeypatch.delenv(data_privacy.SCHEMA_VALUE_HINTS_ENV, raising=False)
    csv = (
        "Date,Particulars,Vch Type,Vch No.,Debit,Contact\n"
        f"2026-11-01,{PARTY},Sales,824,48250.00,{EMAIL}\n"
    ).encode()
    workspace = workspace_core.create_tabular_workspace("Demo", "sales.csv", csv, storage_dir=tmp_path)
    schema = get_schema_snapshot(workspace.database_uri)
    for secret in (PARTY, EMAIL, "48250", "824"):
        assert secret not in schema
    assert "particulars" in schema and "vch_type" in schema
