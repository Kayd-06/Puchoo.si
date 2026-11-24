"""Several files previewed and saved together into one company.

The browser previews each file on its own (/import/preview), the user drops
the ones they don't want, and the rest are saved one by one with
/import/confirm: the first save creates the company (or uses the chosen one)
and every other file goes into that same company with mode "auto". These
tests replay that sequence. All files are the synthetic Demo Traders fixtures.
"""

from __future__ import annotations

import sqlite3

from backend.config import load_settings
from backend.tests.conftest import csrf_headers
from backend.tests.test_company_files import _blob_tables, _files_list, _source_file, _tables
from backend.tests.test_durable_sources import _restart, _wipe
from backend.tests.test_tally_upload import _confirm, _preview, client  # noqa: F401
from tests.fixtures.tally import generate as fx
from apps.core import company_files

FILES = fx.batch_files()
EXPECTED_TABLES = {
    "Sales Register.xlsx": "sales_register",
    "Bank Book.xlsx": "ledger_vouchers",
    "Expenses Ledger.xlsx": "ledger_vouchers_2",
    "Receipt Register.xlsx": "receipt_register",
    "Payment Register.xlsx": "payment_register",
    "Purchase Register.xlsx": "purchase_register",
    "Journal Register.xlsx": "journal_register",
    "Balance Sheet.csv": "balance_sheet",
    "Stock Summary.csv": "stock_summary",
    "Sales Targets.xlsx": "sales_targets",
}


def _previews(test_client, files, target=""):
    previews = {}
    for name, contents in files:
        response = _preview(test_client, name, contents, target_workspace_id=target)
        previews[name] = response
    return previews


def _save_batch(test_client, files, *, target="", name="Demo Traders", confirm=()):
    """What the browser does on Save: one confirm per kept file, all into one company."""

    results = {}
    company_id = target
    for filename, contents in files:
        preview = _preview(test_client, filename, contents, target_workspace_id=company_id)
        fields: dict[str, str] = {}
        if preview.status_code == 200 and preview.json()["kind"] in {"unknown", "table"}:
            # Plain files are saved under the table name shown on their card.
            fields["table_name"] = preview.json()["suggested_table"]
        if preview.status_code == 200 and preview.json()["kind"] == "unknown":
            fields["header_row"] = str(preview.json()["suggested_header_row"])
        elif preview.status_code == 200 and preview.json()["kind"] == "tally" and company_id:
            fields["mode"] = "auto"
        if filename in confirm:
            fields["confirm_company_mismatch"] = "true"
        fields.update({"target_workspace_id": company_id} if company_id else {"name": name})
        response = _confirm(test_client, filename, contents, **fields)
        results[filename] = response
        if response.status_code == 200 and not company_id:
            company_id = response.json()["workspace"]["id"]
    return company_id, results


def test_upload_limit_is_ten_files_and_cannot_be_raised(client, monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_FILES", "20")
    assert load_settings().upload_max_files == 10
    monkeypatch.setenv("UPLOAD_MAX_FILES", "4")
    assert load_settings().upload_max_files == 4

    eleven = [("files", (f"f{index}.csv", b"a,b\n1,2\n", "text/csv")) for index in range(11)]
    response = client.post("/api/workspaces/upload-multiple", files=eleven, headers=csrf_headers(client))
    assert response.status_code == 400
    assert response.json()["detail"] == "You can upload up to 10 files at once. Remove some files and try again."


def test_mixed_files_each_get_a_full_preview_and_nothing_is_saved(client):
    previews = _previews(client, FILES)
    assert all(response.status_code == 200 for response in previews.values()), {
        name: response.text for name, response in previews.items() if response.status_code != 200}
    kinds = {name: response.json()["kind"] for name, response in previews.items()}
    assert list(kinds.values()).count("tally") == 8
    assert kinds["Stock Summary.csv"] == "unknown" and kinds["Sales Targets.xlsx"] == "table"

    sales = previews["Sales Register.xlsx"].json()
    assert sales["report_name"] == "Sales Register" and sales["row_count"] == 12
    assert sales["totals_check"]["status"] == "pass" and sales["sample_rows"]
    assert sales["period_start"] == "2026-11-01"
    stock = previews["Stock Summary.csv"].json()
    assert stock["suggested_header_row"] == 3 and stock["rows"][3] == ["Item", "Qty", "Value"]
    targets = previews["Sales Targets.xlsx"].json()
    assert targets["columns"] == ["month", "sales_target"] and targets["row_count"] == 2
    assert targets["sample_rows"][0] == {"month": "Nov", "sales_target": 400000}
    assert targets["suggested_table"] == "sales_targets"
    assert client.get("/api/workspaces/").json() == []


def test_cancel_two_of_ten_saves_eight_into_one_company_then_the_two_are_added(client):
    cancelled = {"Expenses Ledger.xlsx", "Sales Targets.xlsx"}
    kept = [item for item in FILES if item[0] not in cancelled]
    company_id, results = _save_batch(client, kept)
    assert all(response.status_code == 200 for response in results.values()), {
        name: response.text for name, response in results.items() if response.status_code != 200}
    assert [item["id"] for item in client.get("/api/workspaces/").json()] == [company_id]
    first_eight = sorted(EXPECTED_TABLES[name] for name, _ in kept)
    assert _tables(client, company_id) == first_eight
    rows_before = {table: item["rows"] for table, item in _files_list(client, company_id).items()}

    # Later: the two cancelled files go into the same company. Nothing is replaced.
    later = [item for item in FILES if item[0] in cancelled]
    same_id, later_results = _save_batch(client, later, target=company_id)
    assert same_id == company_id
    assert {name: response.json()["import"]["table"] for name, response in later_results.items()} == {
        "Expenses Ledger.xlsx": "ledger_vouchers_2", "Sales Targets.xlsx": "sales_targets"}
    assert all(response.json()["added_to_existing"] for response in later_results.values())

    assert _tables(client, company_id) == sorted(EXPECTED_TABLES.values())
    files = _files_list(client, company_id)
    assert {table: files[table]["rows"] for table in rows_before} == rows_before
    assert files["ledger_vouchers_2"]["rows"] == 3
    listed = client.get("/api/workspaces/").json()
    assert len(listed) == 1 and listed[0]["file_count"] == 10
    assert sorted(_blob_tables(company_id)) == sorted(EXPECTED_TABLES.values())

    # The durable copy holds every table after the batch: a wiped disk restores all ten.
    _wipe(_source_file(company_id))
    _restart()
    assert _tables(client, company_id) == sorted(EXPECTED_TABLES.values())


def test_same_report_in_a_later_batch_appends_and_skips_saved_vouchers(client):
    company_id, _ = _save_batch(client, FILES[:1])
    dec = fx.sales_register_xlsx(fx.DECEMBER_VOUCHERS, "1-Dec-26 to 31-Dec-26")
    _, results = _save_batch(client, [("Sales Dec.xlsx", dec), ("Sales Nov again.xlsx", fx.sales_register_xlsx())],
                             target=company_id)
    assert results["Sales Dec.xlsx"].json()["import"] == {
        "kind": "tally", "table": "sales_register", "rows_added": 3, "rows_skipped": 0, "rows_replaced": 0}
    assert results["Sales Nov again.xlsx"].json()["import"]["rows_skipped"] == 12
    assert _tables(client, company_id) == ["sales_register"]
    assert _files_list(client, company_id)["sales_register"]["rows"] == 15


def test_one_bad_file_fails_alone_and_leaves_the_company_untouched(client, monkeypatch):
    broken = ("Broken.xlsx", b"PK\x03\x04 this is not a workbook")
    company_id, results = _save_batch(client, [FILES[0], broken, FILES[3]])
    assert results["Broken.xlsx"].status_code == 400
    assert results["Sales Register.xlsx"].status_code == 200
    assert results["Receipt Register.xlsx"].status_code == 200
    assert _tables(client, company_id) == ["receipt_register", "sales_register"]

    # A failure halfway through writing one file rolls that file back only.
    path = _source_file(company_id)
    before = path.read_bytes()
    real = company_files._record_tally

    def explode(*args, **kwargs):
        raise ValueError("disk full")

    monkeypatch.setattr(company_files, "_record_tally", explode)
    failed = _confirm(client, "Purchase Register.xlsx", FILES[5][1], target_workspace_id=company_id, mode="auto")
    assert failed.status_code == 400
    monkeypatch.setattr(company_files, "_record_tally", real)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    later = _confirm(client, "Payment Register.xlsx", FILES[4][1], target_workspace_id=company_id, mode="auto")
    assert later.status_code == 200, later.text
    assert sorted(_blob_tables(company_id)) == ["payment_register", "receipt_register", "sales_register"]


def test_different_company_file_needs_its_own_confirmation(client):
    other = ("Other Sales.xlsx", fx.register_xlsx("Purchase Register", fx.PURCHASES, company="Other Agencies (26-27)"))
    company_id, results = _save_batch(client, [FILES[0], other, FILES[3]])
    assert results["Other Sales.xlsx"].status_code == 409
    assert results["Other Sales.xlsx"].json()["detail"]["code"] == "company_mismatch"
    assert results["Receipt Register.xlsx"].status_code == 200
    preview = _preview(client, *other, target_workspace_id=company_id).json()
    assert preview["company"]["different_company"] is True
    assert _preview(client, *FILES[3], target_workspace_id=company_id).json()["company"]["different_company"] is False

    _, retry = _save_batch(client, [other], target=company_id, confirm={"Other Sales.xlsx"})
    assert retry["Other Sales.xlsx"].status_code == 200, retry["Other Sales.xlsx"].text
    assert _tables(client, company_id) == ["purchase_register", "receipt_register", "sales_register"]


def test_auto_mode_never_overwrites_a_named_table(client):
    company_id, _ = _save_batch(client, FILES[:2])
    clash = _confirm(client, "Receipt Register.xlsx", FILES[3][1], target_workspace_id=company_id,
                     mode="auto", table_name="Ledger Vouchers")
    assert clash.status_code == 400
    assert "already has a table named ledger_vouchers" in clash.json()["detail"]
    renamed = _confirm(client, "Receipt Register.xlsx", FILES[3][1], target_workspace_id=company_id,
                       mode="auto", table_name="Debtor receipts")
    assert renamed.json()["import"]["table"] == "debtor_receipts"
    assert _files_list(client, company_id)["ledger_vouchers"]["rows"] == 5


def test_reupload_never_wipes_a_company_with_several_files(client):
    company_id, _ = _save_batch(client, FILES[:3])
    blocked = client.post(f"/api/workspaces/{company_id}/reupload",
                          files={"file": ("Sales Register.xlsx", FILES[0][1], "application/octet-stream")},
                          headers=csrf_headers(client))
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "company_has_files"
    assert _tables(client, company_id) == ["ledger_vouchers", "ledger_vouchers_2", "sales_register"]


def test_wiped_company_without_a_copy_comes_back_and_takes_more_files(client):
    from backend import database
    from backend.models import WorkspaceSourceBlob

    company_id, _ = _save_batch(client, FILES[:2])
    with database.SessionLocal() as db:
        db.query(WorkspaceSourceBlob).delete()
        db.commit()
    _wipe(_source_file(company_id))
    _restart()
    assert client.get("/api/workspaces/").json()[0]["availability"] == "unavailable"
    restored = client.post(f"/api/workspaces/{company_id}/reupload",
                           files={"file": ("Sales Register.xlsx", FILES[0][1], "application/octet-stream")},
                           headers=csrf_headers(client))
    assert restored.status_code == 200, restored.text
    _, results = _save_batch(client, [FILES[1], FILES[3]], target=company_id)
    assert all(response.status_code == 200 for response in results.values())
    assert _tables(client, company_id) == ["ledger_vouchers", "receipt_register", "sales_register"]


def test_plain_file_results_name_the_table_and_rows(client):
    created = _confirm(client, "Sales Targets.xlsx", FILES[9][1], name="Demo Traders", table_name="sales_targets")
    assert created.json()["import"] == {"kind": "table", "tables": ["sales_targets"], "table": "sales_targets", "rows": 2}
    company_id = created.json()["workspace"]["id"]
    stock = _confirm(client, "Stock Summary.csv", FILES[8][1], target_workspace_id=company_id, header_row="3")
    assert stock.json()["import"]["table"] == "stock_summary" and stock.json()["import"]["rows"] == 2
