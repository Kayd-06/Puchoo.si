"""Company file changes are all-or-nothing and table selection is deterministic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from apps.core import company_files
from apps.core.data_privacy import is_financial_report_file, report_metadata_path
from apps.core.semantic_layer import focus_schema_for_question
from apps.core.workspaces import inspect_upload
from tests.fixtures.tally import generate as fx


def _report(contents=None):
    return inspect_upload("Sales Register.xlsx", contents or fx.sales_register_xlsx()).report


@pytest.fixture
def company(tmp_path: Path) -> Path:
    path = company_files.new_company_file(tmp_path)
    company_files.save_first_tally(path, _report(), label="Sales", source_file="Sales Register.xlsx")
    return path


def test_names_are_safe_and_unique_ignoring_case():
    assert company_files.safe_table_name("Bank Ledger (Nov)") == "bank_ledger_nov"
    assert company_files.safe_table_name("2026 sales") == "t_2026_sales"
    assert company_files.safe_table_name("sqlite_master") == "t_sqlite_master"
    assert company_files.safe_table_name("   ") == "data"
    assert company_files.unique_table_name("Sales_Register", ["sales_register", "SALES_REGISTER_2"]) == "sales_register_3"
    assert len(company_files.unique_table_name("x" * 90, ["x" * 63])) <= 63


def test_company_names_compare_loosely():
    normalize = company_files.normalize_company_name
    assert normalize("Demo Traders (26-27)") == normalize("demo  traders (2027-28)") == "demo traders"
    assert normalize("Demo Traders Pvt. Ltd.") != normalize("Other Traders")


def test_failed_change_leaves_file_and_sidecar_untouched(company: Path):
    before = company.read_bytes()
    sidecar = report_metadata_path(company).read_bytes()
    with pytest.raises(RuntimeError):
        with company_files._working_copy(company) as (working, manifest):
            with sqlite3.connect(working) as connection:
                connection.execute("DROP TABLE sales_register")
            manifest["files"].clear()
            raise RuntimeError("power cut")
    assert company.read_bytes() == before
    assert report_metadata_path(company).read_bytes() == sidecar
    assert not [item for item in company.parent.iterdir() if item.name.startswith(".pucho_change_")]


def test_rollback_snapshot_restores_a_swapped_file(company: Path):
    before = company.read_bytes()
    with company_files.snapshot_for_rollback(company) as restore:
        company_files.add_frames(company, {"s": pd.DataFrame({"a": [1]})}, base_name="extra")
        assert "extra" in company_files.user_tables(company)
        restore()
    assert company.read_bytes() == before
    assert company_files.user_tables(company) == ["sales_register"]


def test_add_many_plain_files_is_all_or_nothing(company: Path):
    good = ({"Sheet1": pd.DataFrame({"a": [1]})}, "targets", "Targets", "targets.csv")
    empty = ({}, "nothing", None, "empty.csv")
    with pytest.raises(company_files.CompanyFileError):
        company_files.add_frame_groups(company, [good, empty])
    assert company_files.user_tables(company) == ["sales_register"]
    assert company_files.add_frame_groups(company, [good]) == ["targets"]


def test_sidecar_tracks_files_and_privacy_flag(company: Path, tmp_path: Path):
    manifest = company_files.read_manifest(company)
    assert manifest["files"]["sales_register"]["label"] == "Sales"
    assert manifest["files"]["sales_register"]["source_file"] == "Sales Register.xlsx"
    assert is_financial_report_file(company)
    plain = company_files.new_company_file(tmp_path)
    company_files.save_first_frames(plain, {"s": pd.DataFrame({"status": ["Paid"]})}, base_name="fees")
    assert not is_financial_report_file(plain)
    report_metadata_path(plain).write_text("not json")
    assert is_financial_report_file(plain)


def test_merge_renames_clashes_and_carries_metadata(company: Path, tmp_path: Path):
    other = company_files.new_company_file(tmp_path)
    december = _report(fx.sales_register_xlsx(fx.DECEMBER_VOUCHERS, "1-Dec-26 to 31-Dec-26"))
    company_files.save_first_tally(other, december)
    result = company_files.merge_into(company, other)
    assert result.tables == {"sales_register": "sales_register_2"}
    assert result.rows == 3
    files = {item["table"]: item for item in company_files.list_files(company)}
    assert files["sales_register_2"]["period_start"] == "2026-12-01"
    assert files["sales_register_2"]["kind"] == "tally"
    assert other.exists()  # The caller removes the moved source after its commit.


def test_merge_refuses_a_missing_or_identical_source(company: Path, tmp_path: Path):
    with pytest.raises(company_files.CompanyFileError):
        company_files.merge_into(company, tmp_path / "gone.db")
    with pytest.raises(company_files.CompanyFileError):
        company_files.merge_into(company, company)


def _schema(names):
    return "\n\n".join(
        f"Table: {name}\nColumns: date (TEXT), party (TEXT), debit_amount (REAL), credit_amount (REAL)"
        for name in names
    )


def test_few_tables_always_keep_the_whole_schema():
    schema = _schema(["sales_register", "bank_ledger", "tp_ledger"])
    assert focus_schema_for_question(schema, "total bank receipts") == (schema, [])


def test_many_tables_keep_only_the_named_ones():
    names = ["sales_register", "bank_ledger", "tp_ledger", "debtors_receipts", "creditors_payments",
             "indirect_expenses", "balance_sheet"]
    schema = _schema(names)
    focused, chosen = focus_schema_for_question(schema, "Compare sales with bank receipts for November")
    assert chosen == ["sales_register", "bank_ledger", "debtors_receipts"]
    assert "Table: creditors_payments" not in focused
    # Shared column words such as "party" or "amount" do not pick every table.
    assert focus_schema_for_question(schema, "party wise amount") == (schema, [])
    # No match at all falls back to every table.
    assert focus_schema_for_question(schema, "कुल बिक्री कितनी है") == (schema, [])


def test_focus_respects_the_cap(monkeypatch):
    names = [f"ledger_{index}" for index in range(10)]
    schema = _schema(names)
    _focused, chosen = focus_schema_for_question(schema, "show every ledger", max_tables=4)
    assert chosen == names[:4]
    monkeypatch.setenv("PUCHOO_SCHEMA_FOCUS_AFTER_TABLES", "20")
    assert focus_schema_for_question(schema, "show every ledger") == (schema, [])
