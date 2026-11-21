"""One company holds many files: Tally reports and plain spreadsheets as tables.

Covers adding files to a company, appending the same report, the
different-company warning, the files list (remove and replace one file),
moving a whole source into a company, durable copies with several tables,
legacy sources, role checks and tenant isolation. The SQL "model" is a local
fake and every row is the synthetic Demo Traders fixture data.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.session import session_manager
from apps.core import company_files
from apps.core.data_privacy import report_metadata_path
from backend import database
from backend.main import create_app
from backend.models import PendingQuery, WorkspaceRecord, WorkspaceSourceBlob
from backend.tests.conftest import csrf_headers, finish_signup, signup_payload
from backend.tests.test_durable_sources import _restart
from backend.tests.test_tally_upload import _ask, _confirm, _files, _preview, client  # noqa: F401
from tests.fixtures.tally import generate as fx

DEC = fx.sales_register_xlsx(fx.DECEMBER_VOUCHERS, "1-Dec-26 to 31-Dec-26")
STOCK = b"Demo Traders stock summary,,\nPrinted 19-Nov-26,,\n,,\nItem,Qty,Value\nCement,10,\"3,500.00\"\nSteel,4,\"12,000.00\"\n"


def _company(test_client, name="Demo Traders"):
    response = _confirm(test_client, "Sales Register.xlsx", fx.sales_register_xlsx(), name=name)
    assert response.status_code == 200, response.text
    return response.json()["workspace"]["id"]


def _add(test_client, workspace_id, filename, contents, **data):
    return _confirm(test_client, filename, contents, target_workspace_id=workspace_id, **data)


def _tables(test_client, workspace_id):
    response = test_client.get(f"/api/workspaces/{workspace_id}/tables")
    assert response.status_code == 200, response.text
    return sorted(table["name"] for table in response.json())


def _files_list(test_client, workspace_id):
    response = test_client.get(f"/api/workspaces/{workspace_id}/files")
    assert response.status_code == 200, response.text
    return {item["table"]: item for item in response.json()["files"]}


def _source_file(workspace_id: str) -> Path:
    workspace = session_manager.workspaces[workspace_id]
    return Path(workspace.get("source_ref") or workspace["database_uri"].removeprefix("sqlite:///"))


def _blob_tables(workspace_id: str) -> list[str]:
    with database.SessionLocal() as db:
        blob = db.get(WorkspaceSourceBlob, workspace_id)
        assert blob is not None
        raw = zlib.decompress(blob.content)
        metadata = json.loads(zlib.decompress(blob.report_metadata)) if blob.report_metadata else {}
    scratch = Path(_source_file(workspace_id)).parent / f"check-{uuid4().hex}.db"
    scratch.write_bytes(raw)
    try:
        with sqlite3.connect(scratch) as connection:
            names = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")]
    finally:
        scratch.unlink()
    assert set(metadata.get("files", {})) == set(names)
    return names


def _member(test_client, otp_codes, role):
    code = test_client.post("/api/v1/auth/workspace/invite", json={"role": role}, headers=csrf_headers(test_client))
    assert code.status_code == 200, code.text
    member = TestClient(create_app(include_product=True))
    member.__enter__()
    email = f"{role}-{uuid4().hex}@tallytest.com"
    member.post("/api/v1/auth/signup", json=signup_payload(email=email, invite_code=code.json()["code"]),
                headers=csrf_headers(member))
    finish_signup(member, email, otp_codes)
    return member


# --------------------------------------------------------------------------
# One company, many files
# --------------------------------------------------------------------------

def test_different_reports_and_plain_files_go_into_one_company(client):
    company_id = _company(client)
    bank = _add(client, company_id, "Bank.xlsx", fx.register_xlsx("Ledger Vouchers", fx.BANK_LEDGER),
                mode="new", table_name="Bank Ledger", label="Bank book")
    assert bank.status_code == 200, bank.text
    assert bank.json()["import"]["table"] == "bank_ledger"
    assert bank.json()["added_to_existing"] is True
    receipts = _add(client, company_id, "Debtors.xlsx", fx.register_xlsx("Receipt Register", fx.DEBTOR_RECEIPTS))
    assert receipts.status_code == 200, receipts.text
    plain = client.post(
        "/api/workspaces/upload", data={"target_workspace_id": company_id},
        files=_files("targets.csv", b"month,target\nNov,400000\n"), headers=csrf_headers(client),
    )
    assert plain.status_code == 200, plain.text
    header = _add(client, company_id, "stock.csv", STOCK, header_row="3", table_name="stock")
    assert header.status_code == 200, header.text

    assert _tables(client, company_id) == sorted(["sales_register", "bank_ledger", "receipt_register", "targets", "stock"])
    listed = client.get("/api/workspaces/").json()
    assert [item["id"] for item in listed] == [company_id]
    assert listed[0]["file_count"] == 5
    files = _files_list(client, company_id)
    assert files["bank_ledger"]["label"] == "Bank book"
    assert files["bank_ledger"]["kind"] == "tally" and files["bank_ledger"]["rows"] == 5
    assert (files["sales_register"]["period_start"], files["sales_register"]["period_end"]) == ("2026-11-01", "2026-11-30")
    assert files["targets"]["kind"] == "table" and files["targets"]["rows"] == 1
    assert all(item["added_at"] for item in files.values())
    schema = client.get(f"/api/workspaces/{company_id}/schema").json()["schema"]
    # The renamed ledger still gets its Tally note; the company header never reaches the schema.
    assert schema.count("Notes: Imported Tally voucher register") == 3
    for private in ("Demo Traders", "Sample Market", "demotraders.example", "Bank book"):
        assert private not in schema


def test_same_report_appends_with_dedupe(client):
    company_id = _company(client)
    preview = _preview(client, "Sales Register.xlsx", fx.sales_register_xlsx(), target_workspace_id=company_id).json()
    assert preview["company"]["suggested_mode"] == "append"
    assert preview["company"]["suggested_table"] == "sales_register"
    assert preview["company"]["existing_vouchers"] == 12
    repeat = _add(client, company_id, "Sales Register.xlsx", fx.sales_register_xlsx(), mode="append", table_name="sales_register")
    assert repeat.json()["import"]["rows_added"] == 0 and repeat.json()["import"]["rows_skipped"] == 12
    december = _add(client, company_id, "Sales Dec.xlsx", DEC, mode="append", table_name="sales_register")
    assert december.json()["import"]["rows_added"] == 3
    assert _tables(client, company_id) == ["sales_register"]
    files = _files_list(client, company_id)
    assert files["sales_register"]["rows"] == 15
    assert files["sales_register"]["period_end"] == "2026-12-31"
    assert files["sales_register"]["uploads"] == 3


def test_a_second_ledger_becomes_its_own_table(client):
    company_id = _company(client)
    _add(client, company_id, "Bank.xlsx", fx.register_xlsx("Ledger Vouchers", fx.BANK_LEDGER), mode="new")
    preview = _preview(client, "Expenses.xlsx", fx.register_xlsx("Ledger Vouchers", fx.EXPENSE_LEDGER),
                       target_workspace_id=company_id).json()
    assert preview["company"]["suggested_mode"] == "new"
    assert preview["company"]["suggested_table"] == "ledger_vouchers_2"
    assert preview["company"]["append_candidates"] == ["ledger_vouchers"]
    check = client.post("/api/workspaces/import/append-check",
                        files=_files("Expenses.xlsx", fx.register_xlsx("Ledger Vouchers", fx.EXPENSE_LEDGER)),
                        data={"target_workspace_id": company_id, "table_name": "ledger_vouchers"},
                        headers=csrf_headers(client))
    # The same payment vouchers show up in both ledgers, so appending would skip them.
    assert check.status_code == 200 and check.json() == {"existing_vouchers": 2, "new_rows": 1}
    saved = _add(client, company_id, "Expenses.xlsx", fx.register_xlsx("Ledger Vouchers", fx.EXPENSE_LEDGER),
                 mode="new", table_name="indirect_expenses")
    assert saved.status_code == 200, saved.text
    # As separate tables, both ledgers keep the shared vouchers P-201 and P-202.
    assert _tables(client, company_id) == sorted(["sales_register", "ledger_vouchers", "indirect_expenses"])
    assert _files_list(client, company_id)["indirect_expenses"]["rows"] == 3


def test_table_name_and_mode_rules(client):
    company_id = _company(client)
    clash = _add(client, company_id, "Sales Dec.xlsx", DEC, mode="new", table_name="Sales_Register")
    assert clash.status_code == 400 and "already has a table" in clash.json()["detail"]
    wrong = _add(client, company_id, "Bank.xlsx", fx.register_xlsx("Balance Sheet", []), mode="append",
                 table_name="sales_register")
    assert wrong.status_code == 400
    bs = _add(client, company_id, "Balance Sheet.csv", fx.balance_sheet_csv(), mode="append", table_name="sales_register")
    assert bs.status_code == 400 and "same kind" in bs.json()["detail"]
    odd = _add(client, company_id, "Sales Dec.xlsx", DEC, mode="merge")
    assert odd.status_code == 400
    weird = _add(client, company_id, "Sales Dec.xlsx", DEC, mode="new", table_name="sqlite_master")
    assert weird.status_code == 200 and weird.json()["import"]["table"] == "t_sqlite_master"


def test_server_connections_cannot_take_files(client):
    company_id = _company(client)
    tenant = session_manager.workspaces[company_id]["owner_user_id"]
    session_manager.workspaces["ws_server_demo"] = {
        "id": "ws_server_demo", "name": "Warehouse DB", "source_type": "server", "owner_user_id": tenant,
        "database_uri": "postgresql://reader@db.example/demo", "dialect": "postgresql",
    }
    try:
        response = _add(client, "ws_server_demo", "Sales Dec.xlsx", DEC)
        assert response.status_code == 400 and "database connection" in response.json()["detail"]
        merge = client.post(f"/api/workspaces/{company_id}/merge", json={"source_workspace_id": "ws_server_demo"},
                            headers=csrf_headers(client))
        assert merge.status_code == 400
    finally:
        session_manager.workspaces.pop("ws_server_demo", None)


# --------------------------------------------------------------------------
# Different company warning
# --------------------------------------------------------------------------

def test_report_from_another_company_needs_explicit_confirmation(client):
    company_id = _company(client)
    other = fx.register_xlsx("Receipt Register", fx.DEBTOR_RECEIPTS, company="Sample Steel Traders (26-27)")
    preview = _preview(client, "Other.xlsx", other, target_workspace_id=company_id).json()
    assert preview["company"]["different_company"] is True
    assert preview["company"]["company_on_file"] == fx.COMPANY
    blocked = _add(client, company_id, "Other.xlsx", other)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "company_mismatch"
    assert _tables(client, company_id) == ["sales_register"]
    allowed = _add(client, company_id, "Other.xlsx", other, confirm_company_mismatch="true")
    assert allowed.status_code == 200, allowed.text
    assert _tables(client, company_id) == sorted(["sales_register", "receipt_register"])


def test_same_company_in_another_financial_year_is_not_a_mismatch(client):
    company_id = _company(client)
    next_year = fx.register_xlsx("Receipt Register", fx.DEBTOR_RECEIPTS, company="demo traders  (2027-28)")
    preview = _preview(client, "Next.xlsx", next_year, target_workspace_id=company_id).json()
    assert preview["company"]["different_company"] is False
    assert _add(client, company_id, "Next.xlsx", next_year).status_code == 200


def test_new_company_preview_has_no_warning(client):
    preview = _preview(client, "Sales Register.xlsx", fx.sales_register_xlsx()).json()
    assert preview["company"]["different_company"] is False
    assert preview["company"]["suggested_table"] == "sales_register"
    assert "append" not in preview


# --------------------------------------------------------------------------
# Files list: remove and replace one file
# --------------------------------------------------------------------------

def test_remove_one_file_keeps_the_others_and_refreshes_the_copy(client):
    company_id = _company(client)
    _add(client, company_id, "Bank.xlsx", fx.register_xlsx("Ledger Vouchers", fx.BANK_LEDGER), mode="new", table_name="bank")
    assert _blob_tables(company_id) == ["sales_register", "bank"]
    removed = client.delete(f"/api/workspaces/{company_id}/files/bank", headers=csrf_headers(client))
    assert removed.status_code == 200, removed.text
    assert removed.json()["workspace"]["file_count"] == 1
    assert _tables(client, company_id) == ["sales_register"]
    assert _blob_tables(company_id) == ["sales_register"]
    manifest = company_files.read_manifest(_source_file(company_id))
    assert all(item["table"] != "bank" for item in manifest["reports"])
    last = client.delete(f"/api/workspaces/{company_id}/files/sales_register", headers=csrf_headers(client))
    assert last.status_code == 400 and "Delete the data source" in last.json()["detail"]
    missing = client.delete(f"/api/workspaces/{company_id}/files/nope", headers=csrf_headers(client))
    assert missing.status_code == 400


def test_removing_a_file_drops_stored_proposals(client):
    company_id = _company(client)
    _add(client, company_id, "Bank.xlsx", fx.register_xlsx("Ledger Vouchers", fx.BANK_LEDGER), mode="new", table_name="bank")
    generated = client.post(f"/api/query/{company_id}/generate", json={"question": "total sales"}, headers=csrf_headers(client))
    assert generated.status_code == 200, generated.text
    with database.SessionLocal() as db:
        assert db.query(PendingQuery).filter(PendingQuery.workspace_id == company_id).count() == 1
    assert client.delete(f"/api/workspaces/{company_id}/files/bank", headers=csrf_headers(client)).status_code == 200
    with database.SessionLocal() as db:
        assert db.query(PendingQuery).filter(PendingQuery.workspace_id == company_id).count() == 0


def test_replace_one_file(client):
    company_id = _company(client)
    _add(client, company_id, "targets.csv", b"month,target\nNov,1\n")
    replaced = client.post(f"/api/workspaces/{company_id}/files/sales_register/replace",
                           files=_files("Sales Dec.xlsx", DEC), headers=csrf_headers(client))
    assert replaced.status_code == 200, replaced.text
    files = _files_list(client, company_id)
    assert files["sales_register"]["rows"] == 3
    assert files["sales_register"]["period_start"] == "2026-12-01"
    assert files["sales_register"]["uploads"] == 1
    assert files["targets"]["rows"] == 1
    plain = client.post(f"/api/workspaces/{company_id}/files/targets/replace",
                        files=_files("targets.csv", b"month,target\nNov,1\nDec,2\n"), headers=csrf_headers(client))
    assert plain.status_code == 200, plain.text
    assert _files_list(client, company_id)["targets"]["rows"] == 2
    unknown = client.post(f"/api/workspaces/{company_id}/files/targets/replace",
                          files=_files("stock.csv", STOCK), headers=csrf_headers(client))
    assert unknown.json()["needs_header_row"] is True
    picked = client.post(f"/api/workspaces/{company_id}/files/targets/replace", data={"header_row": "3"},
                         files=_files("stock.csv", STOCK), headers=csrf_headers(client))
    assert picked.status_code == 200 and _files_list(client, company_id)["targets"]["rows"] == 2
    other = fx.register_xlsx("Sales Register", fx.DEBTOR_RECEIPTS, company="Sample Steel Traders")
    _add(client, company_id, "Bank.xlsx", fx.register_xlsx("Ledger Vouchers", fx.BANK_LEDGER), mode="new", table_name="bank")
    blocked = client.post(f"/api/workspaces/{company_id}/files/bank/replace",
                          files=_files("Other.xlsx", other), headers=csrf_headers(client))
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "company_mismatch"
    assert _blob_tables(company_id) == ["sales_register", "targets", "bank"]


# --------------------------------------------------------------------------
# Move into company (merge)
# --------------------------------------------------------------------------

def test_move_a_source_into_a_company(client, tmp_path):
    company_id = _company(client, "Sample Demo")
    december_id = _confirm(client, "Sales Dec.xlsx", DEC, name="December sales").json()["workspace"]["id"]
    _add(client, december_id, "targets.csv", b"month,target\nNov,1\n")
    _ask(client, december_id, "total sales")
    old_file = _source_file(december_id)
    pending = client.post(f"/api/query/{december_id}/generate", json={"question": "total sales"}, headers=csrf_headers(client))
    assert pending.status_code == 200

    preview = client.get(f"/api/workspaces/{company_id}/merge-preview", params={"source_workspace_id": december_id})
    assert preview.status_code == 200, preview.text
    assert preview.json()["renamed"] == {"sales_register": "sales_register_2"}

    merged = client.post(f"/api/workspaces/{company_id}/merge", json={"source_workspace_id": december_id},
                         headers=csrf_headers(client))
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["tables"] == {"sales_register": "sales_register_2", "targets": "targets"}
    assert body["history_moved"] == 1
    assert body["workspace"]["file_count"] == 3
    assert _tables(client, company_id) == ["sales_register", "sales_register_2", "targets"]
    assert _blob_tables(company_id) == ["sales_register", "sales_register_2", "targets"]

    # The moved source is gone everywhere: list, record, copy, proposals, files on disk.
    assert december_id not in {item["id"] for item in client.get("/api/workspaces/").json()}
    assert client.get(f"/api/workspaces/{december_id}/files").status_code == 404
    with database.SessionLocal() as db:
        assert db.get(WorkspaceRecord, december_id) is None
        assert db.get(WorkspaceSourceBlob, december_id) is None
        assert db.query(PendingQuery).filter(PendingQuery.workspace_id == december_id).count() == 0
    assert not old_file.exists() and not report_metadata_path(old_file).exists()

    files = _files_list(client, company_id)
    assert files["sales_register_2"]["period_start"] == "2026-12-01"
    assert files["targets"]["kind"] == "table"
    history = client.get(f"/api/history/{company_id}")
    assert history.status_code == 200, history.text
    assert [item.get("moved_from") for item in history.json()] == ["December sales"]
    _restart()
    assert [item.get("moved_from") for item in session_manager.get_history(company_id)] == ["December sales"]
    assert december_id not in session_manager.workspaces
    assert _ask(client, company_id, "total sales") == [{"total_sales": pytest.approx(float(fx.NOVEMBER_TOTAL))}]


def test_merge_from_another_company_needs_confirmation(client):
    company_id = _company(client)
    other = fx.register_xlsx("Receipt Register", fx.DEBTOR_RECEIPTS, company="Sample Steel Traders")
    other_id = _confirm(client, "Other.xlsx", other, name="Steel").json()["workspace"]["id"]
    blocked = client.post(f"/api/workspaces/{company_id}/merge", json={"source_workspace_id": other_id},
                          headers=csrf_headers(client))
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "company_mismatch"
    assert other_id in session_manager.workspaces
    assert _tables(client, company_id) == ["sales_register"]
    allowed = client.post(f"/api/workspaces/{company_id}/merge",
                          json={"source_workspace_id": other_id, "confirm_company_mismatch": True},
                          headers=csrf_headers(client))
    assert allowed.status_code == 200, allowed.text


def test_merge_rolls_back_when_the_commit_fails(client, monkeypatch):
    company_id = _company(client)
    other_id = _confirm(client, "Sales Dec.xlsx", DEC, name="Dec").json()["workspace"]["id"]
    target = _source_file(company_id)
    before = target.read_bytes()
    before_sidecar = report_metadata_path(target).read_bytes()

    def broken(*_args, **_kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(session_manager, "absorb_source", broken)
    with pytest.raises(RuntimeError):
        client.post(f"/api/workspaces/{company_id}/merge", json={"source_workspace_id": other_id},
                    headers=csrf_headers(client))
    assert target.read_bytes() == before
    assert report_metadata_path(target).read_bytes() == before_sidecar
    assert other_id in session_manager.workspaces and _source_file(other_id).exists()
    assert _blob_tables(company_id) == ["sales_register"]


def test_merge_rejects_itself_and_unknown_sources(client):
    company_id = _company(client)
    same = client.post(f"/api/workspaces/{company_id}/merge", json={"source_workspace_id": company_id},
                       headers=csrf_headers(client))
    assert same.status_code == 400
    missing = client.post(f"/api/workspaces/{company_id}/merge", json={"source_workspace_id": "ws_missing"},
                          headers=csrf_headers(client))
    assert missing.status_code == 404


def test_other_tenant_cannot_list_change_or_merge(client, otp_codes):
    company_id = _company(client)
    with TestClient(create_app(include_product=True)) as other:
        email = f"other-{uuid4().hex}@elsewhere.com"
        payload = signup_payload(email=email, workspace_type="business", workspace_name="Elsewhere")
        assert other.post("/api/v1/auth/signup", json=payload, headers=csrf_headers(other)).status_code == 202
        finish_signup(other, email, otp_codes)
        own = _confirm(other, "Sales Dec.xlsx", DEC, name="Theirs").json()["workspace"]["id"]
        assert other.get(f"/api/workspaces/{company_id}/files").status_code == 404
        assert other.delete(f"/api/workspaces/{company_id}/files/sales_register", headers=csrf_headers(other)).status_code == 404
        assert other.post(f"/api/workspaces/{company_id}/files/sales_register/replace", files=_files("s.xlsx", DEC),
                          headers=csrf_headers(other)).status_code == 404
        # Pulling my source into their company, or theirs into mine, both fail.
        assert other.post(f"/api/workspaces/{own}/merge", json={"source_workspace_id": company_id},
                          headers=csrf_headers(other)).status_code == 404
        assert other.post(f"/api/workspaces/{company_id}/merge", json={"source_workspace_id": own},
                          headers=csrf_headers(other)).status_code == 404
        assert _confirm(other, "s.xlsx", DEC, target_workspace_id=company_id).status_code == 404
    assert _tables(client, company_id) == ["sales_register"]


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------

def test_role_rules_for_company_files(client, otp_codes):
    company_id = _company(client)
    _add(client, company_id, "Bank.xlsx", fx.register_xlsx("Ledger Vouchers", fx.BANK_LEDGER), mode="new", table_name="bank")
    other_id = _confirm(client, "Sales Dec.xlsx", DEC, name="Dec").json()["workspace"]["id"]
    for role in ("viewer", "editor"):
        member = _member(client, otp_codes, role)
        try:
            assert member.delete(f"/api/workspaces/{company_id}/files/bank", headers=csrf_headers(member)).status_code == 403
            assert member.post(f"/api/workspaces/{company_id}/files/bank/replace", files=_files("s.xlsx", DEC),
                               headers=csrf_headers(member)).status_code == 403
            assert member.post(f"/api/workspaces/{company_id}/merge", json={"source_workspace_id": other_id},
                               headers=csrf_headers(member)).status_code == 403
            assert _confirm(member, "s.xlsx", DEC, target_workspace_id=company_id).status_code == 403
            listing = member.get(f"/api/workspaces/{company_id}/files")
            assert listing.status_code == (200 if role == "editor" else 403)
        finally:
            member.__exit__(None, None, None)
    admin = _member(client, otp_codes, "admin")
    try:
        assert admin.delete(f"/api/workspaces/{company_id}/files/bank", headers=csrf_headers(admin)).status_code == 200
        assert admin.post(f"/api/workspaces/{company_id}/merge", json={"source_workspace_id": other_id},
                          headers=csrf_headers(admin)).status_code == 200
        # Deleting a whole data source stays owner-only (PR #83).
        assert admin.delete(f"/api/workspaces/{company_id}", headers=csrf_headers(admin)).status_code == 403
    finally:
        admin.__exit__(None, None, None)


# --------------------------------------------------------------------------
# Durable copy and legacy sources
# --------------------------------------------------------------------------

def test_wiped_company_is_restored_with_every_table(client):
    company_id = _company(client)
    _add(client, company_id, "Bank.xlsx", fx.register_xlsx("Ledger Vouchers", fx.BANK_LEDGER), mode="new", table_name="bank")
    _add(client, company_id, "targets.csv", b"month,target\nNov,1\n")
    source = _source_file(company_id)
    for candidate in list(source.parent.iterdir()):
        if candidate.name.startswith(source.name):
            candidate.unlink()
    files = _files_list(client, company_id)
    assert list(files) == ["sales_register", "bank", "targets"]
    assert files["bank"]["kind"] == "tally" and files["bank"]["label"] == "Ledger Vouchers"
    assert report_metadata_path(_source_file(company_id)).exists()


def test_legacy_single_report_source_still_works(client):
    """A source saved before this change has the older sidecar with no files list."""

    company_id = _company(client)
    path = _source_file(company_id)
    legacy = company_files.read_manifest(path)
    report_metadata_path(path).write_text(json.dumps({"kind": "tally", "reports": legacy["reports"]}), encoding="utf-8")
    files = _files_list(client, company_id)
    assert files["sales_register"]["label"] == "Sales Register"
    assert files["sales_register"]["rows"] == 12
    assert _ask(client, company_id, "total sales in November") == [{"total_sales": pytest.approx(float(fx.NOVEMBER_TOTAL))}]
    added = _add(client, company_id, "Sales Dec.xlsx", DEC)
    assert added.status_code == 200 and added.json()["import"]["rows_added"] == 3
    assert _tables(client, company_id) == ["sales_register"]


def test_legacy_spreadsheet_without_sidecar_lists_its_tables(client):
    upload = client.post("/api/workspaces/upload", files=_files("marks.csv", b"student,marks\nA,90\n"),
                         headers=csrf_headers(client)).json()
    report_metadata_path(_source_file(upload["id"])).unlink()
    files = _files_list(client, upload["id"])
    assert files["marks"]["kind"] == "table" and files["marks"]["rows"] == 1
    assert _add(client, upload["id"], "attendance.csv", b"student,days\nA,20\n").status_code == 200
    assert _tables(client, upload["id"]) == ["attendance", "marks"]


def test_plain_company_keeps_value_hints_until_a_tally_report_arrives(client, monkeypatch):
    monkeypatch.delenv("PUCHOO_SCHEMA_VALUE_HINTS", raising=False)
    upload = client.post("/api/workspaces/upload", files=_files("fees.csv", b"student,payment_status\nA,Paid\nB,Due\n"),
                         headers=csrf_headers(client)).json()
    schema = client.get(f"/api/workspaces/{upload['id']}/schema").json()["schema"]
    assert "Values for payment_status" in schema
    _add(client, upload["id"], "Sales Register.xlsx", fx.sales_register_xlsx())
    schema = client.get(f"/api/workspaces/{upload['id']}/schema").json()["schema"]
    assert "Values for" not in schema
    assert "Notes: Imported Tally voucher register" in schema


# --------------------------------------------------------------------------
# Cross-table questions
# --------------------------------------------------------------------------

def test_cross_table_question_sends_only_the_tables_it_names(client):
    company_id = _company(client)
    for title, rows, table in (
        ("Ledger Vouchers", fx.BANK_LEDGER, "bank_ledger"),
        ("Ledger Vouchers", fx.EXPENSE_LEDGER, "indirect_expenses"),
        ("Receipt Register", fx.DEBTOR_RECEIPTS, "debtors_receipts"),
        ("Payment Register", fx.CREDITOR_PAYMENTS, "creditors_payments"),
        ("Ledger Vouchers", fx.BANK_LEDGER[:2], "tp_ledger"),
    ):
        response = _add(client, company_id, f"{table}.xlsx", fx.register_xlsx(title, rows), mode="new", table_name=table)
        assert response.status_code == 200, response.text
    _add(client, company_id, "Balance Sheet.csv", fx.balance_sheet_csv())

    sql = (
        "SELECT (SELECT SUM(debit_amount) FROM sales_register) AS total_sales, "
        "(SELECT SUM(debit_amount) FROM bank_ledger WHERE voucher_type = 'Receipt') AS bank_receipts"
    )
    seen: list[str] = []

    class CrossTableClient:
        provider_name = "local test client"

        def generate_sql(self, *, schema, question, feedback=None, previous_sql=None):
            seen.append(schema)
            return sql

    from apps.api.routers import query as query_router
    original = query_router.get_sql_client
    query_router.get_sql_client = lambda: CrossTableClient()
    try:
        rows = _ask(client, company_id, "compare sales with bank receipts")
    finally:
        query_router.get_sql_client = original
    assert rows == [{"total_sales": pytest.approx(float(fx.NOVEMBER_TOTAL)), "bank_receipts": pytest.approx(123850.0)}]
    schema = seen[-1]
    assert "Table: sales_register" in schema and "Table: bank_ledger" in schema
    for dropped in ("creditors_payments", "indirect_expenses", "balance_sheet"):
        assert f"Table: {dropped}" not in schema
