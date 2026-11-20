"""Tally report importer: detection, parsing, totals, dedupe, number formats, fallback.

Uses only the synthetic 'Demo Traders' fixtures in tests/fixtures/tally.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from apps.core import tally_import as ti
from apps.core.upload_limits import read_raw_grid
from tests.fixtures.tally import generate as fx


def _grid(name: str, contents: bytes):
    return next(iter(read_raw_grid(name, contents).values()))


def _sales(**kwargs):
    return ti.parse_tally_report(_grid("sales.xlsx", fx.sales_register_xlsx(**kwargs)))


def _balance(**kwargs):
    return ti.parse_tally_report(_grid("bs.csv", fx.balance_sheet_csv(**kwargs)))


def test_committed_fixtures_match_the_generator():
    assert (fx.HERE / "sales_register_nov26.xlsx").is_file()
    assert (fx.HERE / "balance_sheet_19nov26.csv").read_bytes() == fx.balance_sheet_csv()


@pytest.mark.parametrize(
    ("report_name", "table"),
    [("Sales Register", "sales_register"), ("Purchase Register", "purchase_register"), ("Day Book", "day_book")],
)
def test_voucher_registers_are_detected_from_the_title_row(report_name, table):
    report = _sales(report_name=report_name)
    assert report.report_type == ti.VOUCHER_REGISTER
    assert report.table == table
    assert report.metadata.report_name == report_name


def test_sales_register_strips_header_block_and_keeps_it_as_metadata():
    report = _sales()
    assert report.metadata.company_name == "Demo Traders (26-27)"
    assert (report.metadata.period_start, report.metadata.period_end) == ("2026-11-01", "2026-11-30")
    flat = repr(report.rows) + repr(report.columns)
    for header_text in ("Demo Traders", "Sample Market", "Pune", "90000", "demotraders.example", "Contact", "E-Mail"):
        assert header_text not in flat


def test_sales_register_rows_are_clean_and_typed():
    report = _sales()
    assert report.columns == [
        "date", "party", "voucher_type", "voucher_no", "debit_amount", "credit_amount",
        "period_start", "period_end", "source_report",
    ]
    assert len(report.rows) == len(fx.NOVEMBER_VOUCHERS)
    first = report.rows[0]
    assert first == {
        "date": "2026-11-01", "party": "Sharma Hardware Stores", "voucher_type": "Sales",
        "voucher_no": "820", "debit_amount": 48250.0, "credit_amount": None,
        "period_start": "2026-11-01", "period_end": "2026-11-30", "source_report": "Sales Register",
    }
    assert all(isinstance(row["voucher_no"], str) for row in report.rows)
    assert not any(str(row["party"]).lower().startswith("total") for row in report.rows)


def test_sales_register_total_is_reconciled_not_imported():
    report = _sales()
    assert report.totals_status == "pass"
    assert report.checks[0].expected == float(fx.NOVEMBER_TOTAL)
    assert sum(row["debit_amount"] for row in report.rows) == pytest.approx(float(fx.NOVEMBER_TOTAL))


def test_sales_register_total_mismatch_is_a_warning_not_silent():
    report = _sales(total=fx.NOVEMBER_TOTAL + 100)
    assert report.totals_status == "fail"
    assert any("Tally shows" in warning for warning in report.warnings)


def test_balance_sheet_becomes_long_format_with_both_sides():
    report = _balance()
    assert report.report_type == ti.BALANCE_SHEET
    assert report.metadata.report_date == "2026-11-19"
    assert report.metadata.period_start == "2026-04-01"
    assert report.metadata.company_name == "Demo Traders (26-27)"
    groups = {(row["side"], row["account"]): row for row in report.rows if row["row_type"] == "group"}
    assert groups[("asset", "Current Assets")]["amount"] == fx.CURRENT_ASSETS_TOTAL
    assert groups[("liability", "Capital Account")]["amount"] == 500000.0
    drawings = next(row for row in report.rows if row["account"] == "Drawings")
    assert drawings == {**drawings, "row_type": "item", "group_name": "Capital Account", "amount": -100000.0}
    current_period = next(row for row in report.rows if row["account"] == "Current Period")
    assert current_period["amount"] == -792625.0
    assert not any(str(row["account"]).lower() == "total" for row in report.rows)
    assert "Demo Traders" not in repr(report.rows)


def test_balance_sheet_reconciles_groups_sides_and_totals():
    report = _balance()
    assert report.totals_status == "pass", report.checks
    labels = [check.label for check in report.checks]
    assert "Liability total equals asset total" in labels
    assert "Asset group 'Current Assets': items vs group amount" in labels


def test_balance_sheet_flags_suspense_and_opening_difference():
    warnings = " ".join(_balance().warnings)
    assert "Suspense A/c" in warnings
    assert "Difference in opening balances of 5,000.00" in warnings


def test_balance_sheet_unequal_sides_fail_the_check():
    report = _balance(right_total="1049000.00")
    assert report.totals_status == "fail"
    assert any("does not equal" in warning for warning in report.warnings)


def test_numbers_title_line_with_or_without_padding_is_skipped():
    assert _balance(pad_title=True).rows == _balance(pad_title=False).rows


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,23,456.00", Decimal("123456.00")),
        ("-792625.00", Decimal("-792625.00")),
        ("(1,234.00)", Decimal("-1234.00")),
        ("5,000.00 Dr", Decimal("5000.00")),
        ("300.50 Cr", Decimal("-300.50")),
        ("12,34,56,789.10", Decimal("123456789.10")),
        ("₹ 1,000", Decimal("1000")),
        (48250.0, Decimal("48250.0")),
        ("", None),
        ("Sales", None),
        ("1-Nov-26", None),
    ],
)
def test_indian_amount_formats(text, expected):
    assert ti.parse_amount(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1-Nov-26", date(2026, 11, 1)), ("30-Nov-2026", date(2026, 11, 30)), ("2026-11-05", date(2026, 11, 5)),
     ("05-11-2026", date(2026, 11, 5)), ("31-Feb-26", None), ("Total:", None)],
)
def test_tally_dates(text, expected):
    assert ti.parse_date(text) == expected


def test_comma_amounts_and_dr_cr_inside_a_balance_sheet():
    liabilities = [("Capital Account", None, "5,00,000.00"), ("Owner Capital", "(1,00,000.00)", None), ("Partner", "6,00,000.00", None)]
    assets = [("Current Assets", None, "5,00,000.00 Dr"), ("Cash-in-Hand", "5,00,000.00", None)]
    report = _balance(liabilities=liabilities, assets=assets, left_total="5,00,000.00", right_total="5,00,000.00")
    assert report.totals_status == "pass", report.checks
    assert next(row for row in report.rows if row["account"] == "Owner Capital")["amount"] == -100000.0


def _save(report, path):
    return ti.save_report(report, path)


def _count(path, table):
    with sqlite3.connect(path) as connection:
        return connection.execute(f"SELECT COUNT(*), SUM(debit_amount) FROM {table}").fetchone()


def test_reuploading_the_same_register_does_not_double_count(tmp_path):
    db = tmp_path / "w.db"
    first = _save(_sales(), db)
    again = _save(_sales(), db)
    assert (first.rows_added, again.rows_added, again.rows_skipped) == (12, 0, 12)
    assert _count(db, "sales_register") == (12, pytest.approx(float(fx.NOVEMBER_TOTAL)))


def test_next_month_appends_and_overlap_adds_only_new_vouchers(tmp_path):
    db = tmp_path / "w.db"
    _save(_sales(), db)
    december = _save(_sales(vouchers=fx.DECEMBER_VOUCHERS, period="1-Dec-26 to 31-Dec-26"), db)
    assert december.rows_added == 3
    overlap = fx.NOVEMBER_VOUCHERS[-2:] + fx.DECEMBER_VOUCHERS[:1]
    third = _save(_sales(vouchers=overlap, period="27-Nov-26 to 2-Dec-26"), db)
    assert (third.rows_added, third.rows_skipped) == (0, 3)
    assert _count(db, "sales_register")[0] == 15
    assert ti.existing_voucher_overlap(_sales(), db) == 12


def test_same_vch_no_on_a_different_date_is_a_different_voucher(tmp_path):
    db = tmp_path / "w.db"
    _save(_sales(), db)
    from datetime import datetime
    moved = [(datetime(2027, 4, 1), "Gupta Electricals", "820", "100.00")]
    assert _save(_sales(vouchers=moved, period="1-Apr-27 to 30-Apr-27"), db).rows_added == 1


def test_balance_sheet_for_the_same_date_replaces_the_snapshot(tmp_path):
    db = tmp_path / "w.db"
    first = _save(_balance(), db)
    second = _save(_balance(), db)
    assert second.rows_replaced == first.rows_added
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT amount FROM balance_sheet WHERE row_type='group' AND account='Current Assets'"
        ).fetchall() == [(fx.CURRENT_ASSETS_TOTAL,)]
        later = _save(_balance(as_at="31-Mar-27", period="1-Apr-26 to 31-Mar-27"), db)
        assert later.rows_replaced == 0
        assert connection.execute("SELECT COUNT(DISTINCT report_date) FROM balance_sheet").fetchone() == (2,)


def test_metadata_sidecar_keeps_company_details(tmp_path):
    report = _sales()
    result = _save(report, tmp_path / "w.db")
    payload = ti.write_metadata(tmp_path / "w.db.report.json", report, result)
    assert payload["reports"][0]["company_name"] == "Demo Traders (26-27)"
    assert ti.companies_in(ti.read_metadata(tmp_path / "w.db.report.json")) == {"Demo Traders (26-27)"}


def test_trial_balance_parses_debit_credit_and_grand_total():
    grid = [
        ["Demo Traders (26-27)"], ["Trial Balance"], ["1-Apr-26 to 19-Nov-26"],
        ["Particulars", "Closing Balance", ""], ["", "Debit", "Credit"],
        ["Capital Account", "", "5,00,000.00"], ["Current Assets", "4,00,000.00", ""],
        ["Fixed Assets", "1,00,000.00", ""], ["Grand Total", "5,00,000.00", "5,00,000.00"],
    ]
    report = ti.parse_tally_report(grid)
    assert report.report_type == ti.TRIAL_BALANCE
    assert report.totals_status == "pass", report.checks
    assert [row["account"] for row in report.rows] == ["Capital Account", "Current Assets", "Fixed Assets"]


def test_profit_and_loss_sides_are_expense_and_income():
    grid = [
        ["Demo Traders (26-27)"], ["Profit & Loss A/c"], ["1-Apr-26 to 19-Nov-26"],
        ["Particulars", "1-Apr-26 to 19-Nov-26", "", "Particulars", "1-Apr-26 to 19-Nov-26", ""],
        ["Purchase Accounts", "", "300000.00", "Sales Accounts", "", "476000.00"],
        ["Indirect Expenses", "", "76000.00", "", "", ""],
        ["Rent", "50000.00", "", "", "", ""],
        ["Salary", "26000.00", "", "", "", ""],
        ["Nett Profit", "", "100000.00", "", "", ""],
        ["Total", "", "476000.00", "Total", "", "476000.00"],
    ]
    report = ti.parse_tally_report(grid)
    assert report.report_type == ti.PROFIT_AND_LOSS
    assert {row["side"] for row in report.rows} == {"expense", "income"}
    assert report.totals_status == "pass", report.checks


def test_unknown_layout_offers_rows_and_a_suggested_header():
    grid = [["Demo Traders branch report"], ["Printed on 19-Nov-26"], [], ["Item", "Qty", "Rate"], ["Cement", "10", "350"]]
    assert ti.parse_tally_report(grid) is None
    assert ti.looks_like_report_layout(grid)
    assert ti.suggest_header_row(grid) == 3
    assert len(ti.preview_rows(grid)) == 5
    names, rows = ti.table_from_header_row(grid, 3)
    assert names == ["Item", "Qty", "Rate"] and rows == [["Cement", "10", "350"]]


def test_plain_tables_are_not_treated_as_reports():
    grid = [["month", "revenue"], ["Jan", "120"], ["Feb", "160"]]
    assert ti.parse_tally_report(grid) is None
    assert not ti.looks_like_report_layout(grid)


def test_header_row_must_exist():
    with pytest.raises(ti.TallyImportError):
        ti.table_from_header_row([["a"]], 5)


def test_schema_note_is_fixed_text():
    note = ti.schema_note("balance_sheet")
    assert "row_type" in note and "'liability' or 'asset'" in note
    assert ti.schema_note("sales_register").startswith("Imported Tally voucher register")
    assert ti.schema_note("orders") is None


def test_raw_reader_respects_upload_limits(monkeypatch):
    from apps.core.upload_limits import UploadLimitError
    monkeypatch.setenv("UPLOAD_MAX_ROWS", "5")
    with pytest.raises(UploadLimitError):
        read_raw_grid("bs.csv", fx.balance_sheet_csv())
    with pytest.raises(UploadLimitError):
        read_raw_grid("sales.xlsx", fx.sales_register_xlsx())
