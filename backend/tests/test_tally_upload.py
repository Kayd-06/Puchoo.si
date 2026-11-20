"""Tally uploads over HTTP: preview, confirm, append, and asking questions.

The SQL "model" here is a local fake that maps a question to SQL, so no
provider is called. All data is the synthetic Demo Traders fixture set.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.routers import query as query_router
from apps.api.session import session_manager
from apps.core import workspaces as workspace_core
from apps.core.query_planning import ComplexityDecision
from backend.main import create_app
from backend.tests.conftest import csrf_headers, finish_signup, signup_payload
from tests.fixtures.tally import generate as fx

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
QUESTION_SQL = {
    "total sales in november": (
        "SELECT SUM(debit_amount) AS total_sales FROM sales_register "
        "WHERE date BETWEEN '2026-11-01' AND '2026-11-30'"
    ),
    "total sales": "SELECT SUM(debit_amount) AS total_sales FROM sales_register",
    "total current assets": (
        "SELECT amount AS total_current_assets FROM balance_sheet "
        "WHERE row_type = 'group' AND side = 'asset' AND account = 'Current Assets'"
    ),
}


class LocalQuestionClient:
    provider_name = "local test client"

    def __init__(self) -> None:
        self.schemas: list[str] = []

    def generate_sql(self, *, schema, question, feedback=None, previous_sql=None):
        self.schemas.append(schema)
        text = question.lower()
        if "current assets" in text:
            return QUESTION_SQL["total current assets"]
        if "november" in text or "2026-11" in text:
            return QUESTION_SQL["total sales in november"]
        return QUESTION_SQL["total sales"]


class NoVerifier:
    def verify(self, **_kwargs):
        class Result:
            def as_dict(self):
                return {"status": "VERIFIED", "summary": "ok", "details": ""}
        return Result()


class NoMemory:
    def search_approved_conversations(self, **_kwargs):
        return []

    def save_approved_conversation(self, **_kwargs):
        return None


@pytest.fixture
def client(app, otp_codes, tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_core, "LOCAL_WORKSPACE_DIRECTORY", tmp_path)
    monkeypatch.setattr("apps.api.session.save_local_state", lambda **kwargs: None)
    fake = LocalQuestionClient()
    monkeypatch.setattr(query_router, "get_sql_client", lambda: fake)
    monkeypatch.setattr(query_router, "schema_guided_fallback_sql", lambda *_a, **_k: None)
    monkeypatch.setattr(query_router, "classify_complexity", lambda *_a, **_k: ComplexityDecision("simple", 0, ()))
    monkeypatch.setattr(query_router, "get_verifier", lambda: NoVerifier())
    monkeypatch.setattr(query_router, "get_chat_memory", lambda: NoMemory())
    with TestClient(create_app(include_product=True)) as test_client:
        email = f"owner-{uuid4().hex}@tallytest.com"
        payload = signup_payload(email=email, workspace_type="business", workspace_name="Demo Traders")
        assert test_client.post("/api/v1/auth/signup", json=payload, headers=csrf_headers(test_client)).status_code == 202
        finish_signup(test_client, email, otp_codes)
        test_client.fake = fake
        yield test_client


def _files(filename: str, contents: bytes):
    kind = "text/csv" if filename.endswith(".csv") else XLSX
    return {"file": (filename, contents, kind)}


def _preview(client, filename, contents, **data):
    return client.post("/api/workspaces/import/preview", files=_files(filename, contents), data=data, headers=csrf_headers(client))


def _confirm(client, filename, contents, **data):
    return client.post("/api/workspaces/import/confirm", files=_files(filename, contents), data=data, headers=csrf_headers(client))


def _ask(client, workspace_id, question):
    generated = client.post(f"/api/query/{workspace_id}/generate", json={"question": question}, headers=csrf_headers(client))
    assert generated.status_code == 200, generated.text
    proposal = generated.json()
    executed = client.post(
        f"/api/query/{workspace_id}/pending/{proposal['pending_id']}/approve",
        json={"sql_hash": proposal["sql_hash"]},
        headers=csrf_headers(client),
    )
    assert executed.status_code == 200, executed.text
    return executed.json()["record"]["rows"]


def test_preview_describes_the_sales_register_without_saving(client, tmp_path):
    before = set(tmp_path.iterdir())
    response = _preview(client, "Sales Register.xlsx", fx.sales_register_xlsx())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "tally"
    assert body["report_name"] == "Sales Register"
    assert (body["period_start"], body["period_end"]) == ("2026-11-01", "2026-11-30")
    assert body["columns"][:6] == ["date", "party", "voucher_type", "voucher_no", "debit_amount", "credit_amount"]
    assert body["row_count"] == 12 and len(body["sample_rows"]) == 8
    assert body["totals_check"]["status"] == "pass"
    assert set(tmp_path.iterdir()) == before


def test_preview_of_the_balance_sheet_shows_warnings(client):
    body = _preview(client, "Balance Sheet.csv", fx.balance_sheet_csv()).json()
    assert body["report_type"] == "balance_sheet"
    assert body["totals_check"]["status"] == "pass"
    assert any("Suspense" in warning for warning in body["warnings"])
    assert any("Difference in opening balances" in warning for warning in body["warnings"])


def test_numbers_files_are_rejected_with_export_steps(client):
    response = _preview(client, "Balance Sheet.numbers", b"PK\x03\x04numbers")
    assert response.status_code == 400
    assert "Alt+E" in response.json()["detail"] and "Export To" in response.json()["detail"]
    upload = client.post("/api/workspaces/upload", files=_files("x.numbers", b"PK\x03\x04"), headers=csrf_headers(client))
    assert upload.status_code == 400 and "Numbers" in upload.json()["detail"]


def test_confirmed_sales_register_answers_total_sales_in_november(client):
    confirmed = _confirm(client, "Sales Register.xlsx", fx.sales_register_xlsx(), name="Demo sales")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["workspace"]["source_type"] == "tally_report"
    assert body["import"] == {"kind": "tally", "table": "sales_register", "rows_added": 12, "rows_skipped": 0, "rows_replaced": 0}
    rows = _ask(client, body["workspace"]["id"], "total sales in November")
    assert rows == [{"total_sales": pytest.approx(float(fx.NOVEMBER_TOTAL))}]
    schema = client.fake.schemas[-1]
    assert "Notes: Imported Tally voucher register" in schema
    for private in ("Demo Traders", "Sample Market", "demotraders.example", "90000", "Sharma Hardware", "48250"):
        assert private not in schema


def test_balance_sheet_group_total_is_not_double_counted(client):
    body = _confirm(client, "Balance Sheet.csv", fx.balance_sheet_csv()).json()
    rows = _ask(client, body["workspace"]["id"], "total current assets")
    assert rows == [{"total_current_assets": fx.CURRENT_ASSETS_TOTAL}]
    assert "row_type is 'group'" in client.fake.schemas[-1]


def test_appending_months_and_reuploading_does_not_double_count(client):
    workspace_id = _confirm(client, "Sales Register.xlsx", fx.sales_register_xlsx()).json()["workspace"]["id"]
    again = _preview(client, "Sales Register.xlsx", fx.sales_register_xlsx(), target_workspace_id=workspace_id).json()
    assert again["append"] == {"existing_vouchers": 12, "new_rows": 0, "different_company": False}
    repeat = _confirm(client, "Sales Register.xlsx", fx.sales_register_xlsx(), target_workspace_id=workspace_id).json()
    assert repeat["import"]["rows_added"] == 0 and repeat["import"]["rows_skipped"] == 12
    december = fx.sales_register_xlsx(fx.DECEMBER_VOUCHERS, "1-Dec-26 to 31-Dec-26")
    added = _confirm(client, "Sales Register Dec.xlsx", december, target_workspace_id=workspace_id).json()
    assert added["import"]["rows_added"] == 3
    expected = float(fx.NOVEMBER_TOTAL) + sum(float(amount) for *_, amount in fx.DECEMBER_VOUCHERS)
    assert _ask(client, workspace_id, "total sales") == [{"total_sales": pytest.approx(expected)}]
    assert _ask(client, workspace_id, "total sales in November") == [{"total_sales": pytest.approx(float(fx.NOVEMBER_TOTAL))}]


def test_only_tally_sources_can_be_extended(client):
    plain = client.post(
        "/api/workspaces/upload", files=_files("plain.csv", b"month,revenue\nJan,1\n"), headers=csrf_headers(client)
    ).json()
    response = _confirm(client, "Sales Register.xlsx", fx.sales_register_xlsx(), target_workspace_id=plain["id"])
    assert response.status_code == 400
    assert _confirm(client, "s.xlsx", fx.sales_register_xlsx(), target_workspace_id="ws_missing").status_code == 404


def test_old_upload_endpoint_also_routes_tally_files_through_the_importer(client):
    response = client.post("/api/workspaces/upload", files=_files("Sales Register.xlsx", fx.sales_register_xlsx()), headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    workspace = session_manager.workspaces[response.json()["id"]]
    assert workspace["source_type"] == "tally_report"
    tables = client.get(f"/api/workspaces/{workspace['id']}/tables").json()
    assert [table["name"] for table in tables] == ["sales_register"]


def test_tally_reports_must_be_uploaded_one_at_a_time(client):
    response = client.post(
        "/api/workspaces/upload-multiple",
        files=[("files", ("a.csv", b"x,y\n1,2\n", "text/csv")), ("files", ("bs.csv", fx.balance_sheet_csv(), "text/csv"))],
        headers=csrf_headers(client),
    )
    assert response.status_code == 400
    assert "one at a time" in response.json()["detail"]


def test_unknown_layout_returns_rows_and_imports_with_the_chosen_header(client):
    contents = b"Demo Traders stock summary,,\nPrinted 19-Nov-26,,\n,,\nItem,Qty,Value\nCement,10,\"3,500.00\"\nSteel,4,\"12,000.00\"\n"
    preview = _preview(client, "stock.csv", contents).json()
    assert preview["kind"] == "unknown"
    assert preview["suggested_header_row"] == 3
    assert preview["rows"][3] == ["Item", "Qty", "Value"]
    confirmed = _confirm(client, "stock.csv", contents, header_row="3", name="Stock")
    assert confirmed.status_code == 200, confirmed.text
    schema = client.get(f"/api/workspaces/{confirmed.json()['workspace']['id']}/schema").json()["schema"]
    assert "Columns: item (TEXT), qty (REAL), value (REAL)" in schema
    assert _preview(client, "plain.csv", b"month,revenue\nJan,1\n").json() == {"kind": "table"}
    assert _confirm(client, "stock.csv", contents, header_row="99").status_code == 400


def test_viewers_cannot_preview_or_import(client, otp_codes):
    code = client.post("/api/v1/auth/workspace/invite", json={"role": "viewer"}, headers=csrf_headers(client)).json()["code"]
    with TestClient(create_app(include_product=True)) as viewer:
        email = f"viewer-{uuid4().hex}@tallytest.com"
        viewer.post("/api/v1/auth/signup", json=signup_payload(email=email, workspace_type="personal", invite_code=code), headers=csrf_headers(viewer))
        finish_signup(viewer, email, otp_codes)
        assert _preview(viewer, "s.xlsx", fx.sales_register_xlsx()).status_code == 403
        assert _confirm(viewer, "s.xlsx", fx.sales_register_xlsx()).status_code == 403


def test_deleting_a_tally_source_removes_its_metadata(client, tmp_path):
    workspace_id = _confirm(client, "Sales Register.xlsx", fx.sales_register_xlsx()).json()["workspace"]["id"]
    assert any(path.name.endswith(".report.json") for path in tmp_path.iterdir())
    assert client.delete(f"/api/workspaces/{workspace_id}", headers=csrf_headers(client)).status_code == 204
    assert not any(path.name.startswith("pucho_tally_") for path in tmp_path.iterdir())
