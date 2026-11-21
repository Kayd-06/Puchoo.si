"""Synthetic Tally exports for tests. Every name, phone, email and amount is fake.

The layouts copy what TallyPrime / Tally.ERP 9 produce so the importer can be
tested without real company data. Run this file to regenerate the fixtures:

    python -m tests.fixtures.tally.generate
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPANY = "Demo Traders (26-27)"

NOVEMBER_VOUCHERS = [
    (datetime(2026, 11, 1), "Sharma Hardware Stores", "820", "48250.00"),
    (datetime(2026, 11, 3), "Gupta Electricals", "821", "15600.00"),
    (datetime(2026, 11, 3), "Mehta Paints & Co", "822", "9875.50"),
    (datetime(2026, 11, 7), "Sharma Hardware Stores", "823", "22000.00"),
    (datetime(2026, 11, 10), "Patel Building Supplies", "824", "131400.00"),
    (datetime(2026, 11, 12), "Gupta Electricals", "825", "7420.25"),
    (datetime(2026, 11, 15), "Rao Timber Mart", "826", "56300.00"),
    (datetime(2026, 11, 18), "Sharma Hardware Stores", "827", "18990.00"),
    (datetime(2026, 11, 21), "Iyer Tiles Depot", "828", "40250.75"),
    (datetime(2026, 11, 24), "Mehta Paints & Co", "829", "12100.00"),
    (datetime(2026, 11, 27), "Patel Building Supplies", "830", "88000.00"),
    (datetime(2026, 11, 30), "Rao Timber Mart", "831", "25813.50"),
]
NOVEMBER_TOTAL = sum((Decimal(amount) for *_, amount in NOVEMBER_VOUCHERS), Decimal(0))

DECEMBER_VOUCHERS = [
    (datetime(2026, 12, 2), "Sharma Hardware Stores", "832", "30500.00"),
    (datetime(2026, 12, 9), "Iyer Tiles Depot", "833", "14250.00"),
    (datetime(2026, 12, 16), "Gupta Electricals", "834", "9800.00"),
]


def sales_register_xlsx(
    vouchers=NOVEMBER_VOUCHERS,
    period: str = "1-Nov-26 to 30-Nov-26",
    *,
    total: Decimal | None = None,
    report_name: str = "Sales Register",
) -> bytes:
    """Build a Tally 'Sales Register' Excel export (single sheet, 6 columns)."""

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = report_name
    header_block = [
        COMPANY,
        "Shop No. 12, Sample Market Road",
        "Demo Nagar, Pune - 411000",
        "Contact : 90000 11111, 90000 22222",
        "E-Mail : accounts@demotraders.example",
        report_name,
        period,
    ]
    for row, text in enumerate(header_block, start=1):
        sheet.cell(row=row, column=1, value=text)
        if row != 6:
            sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
    for column, text in enumerate(["Date", "Particulars", "Vch Type", "Vch No.", "Debit", "Credit"], start=1):
        sheet.cell(row=8, column=column, value=text)
    for column, text in enumerate(["", "", "", "", "Amount", "Amount"], start=1):
        sheet.cell(row=9, column=column, value=text or None)
    row = 10
    for when, party, number, amount in vouchers:
        sheet.cell(row=row, column=1, value=when)
        sheet.cell(row=row, column=2, value=party)
        sheet.cell(row=row, column=3, value="Sales")
        sheet.cell(row=row, column=4, value=number)
        sheet.cell(row=row, column=5, value=float(amount))
        sheet.cell(row=row, column=6, value=None)
        row += 1
    computed = sum((Decimal(amount) for *_, amount in vouchers), Decimal(0))
    sheet.cell(row=row, column=1, value="Total:")
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    sheet.cell(row=row, column=5, value=float(total if total is not None else computed))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


LIABILITIES = [
    ("Capital Account", None, "500000.00"),
    ("Owner Capital", "600000.00", None),
    ("Drawings", "-100000.00", None),
    ("Loans (Liability)", None, "250000.00"),
    ("Demo Bank OD A/c", "250000.00", None),
    ("Current Liabilities", None, "182625.00"),
    ("Duties & Taxes", "42625.00", None),
    ("Sundry Creditors", "140000.00", None),
    # Zero-balance line printed with both amount columns empty, between the
    # Current Liabilities items and the next group (as Tally exports it).
    ("Suspense A/c", None, None),
    ("Profit & Loss A/c", None, "117375.00"),
    ("Opening Balance", "910000.00", None),
    ("Current Period", "-792625.00", None),
]
ASSETS = [
    ("Fixed Assets", None, "300000.00"),
    ("Furniture", "120000.00", None),
    ("Computers", "180000.00", None),
    ("Investments", None, "50000.00"),
    ("Fixed Deposit", "50000.00", None),
    ("Current Assets", None, "695000.00"),
    ("Closing Stock", "300000.00", None),
    ("Deposits (Asset)", "20000.00", None),
    # Empty sub-group in the middle of Current Assets; the items below it
    # still belong to Current Assets.
    ("Loans & Advances (Asset)", None, None),
    ("Sundry Debtors", "225000.00", None),
    ("Cash-in-Hand", "25000.00", None),
    ("Bank Accounts", "120000.00", None),
    ("GST Receivable", "3000.00", None),
    ("TCS", "2000.00", None),
    ("Difference in opening balances", None, "5000.00"),
]
BALANCE_TOTAL = "1050000.00"
CURRENT_ASSETS_TOTAL = 695000.00


def balance_sheet_csv(
    liabilities=LIABILITIES,
    assets=ASSETS,
    *,
    left_total: str = BALANCE_TOTAL,
    right_total: str = BALANCE_TOTAL,
    as_at: str = "19-Nov-26",
    period: str = "1-Apr-26 to 19-Nov-26",
    pad_title: bool = False,
) -> bytes:
    """Build a Tally Balance Sheet that went through Apple Numbers -> CSV."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    buffer.write("Table 1,,,,,\n" if pad_title else "Table 1\n")
    writer.writerow([COMPANY, "", "", "", "", ""])
    for _ in range(3):
        writer.writerow(["", "", "", "", "", ""])
    writer.writerow(["Balance Sheet", "", "", "", "", ""])
    writer.writerow([period, "", "", "", "", ""])
    writer.writerow(["", "", "", "", "", ""])
    writer.writerow(["Liabilities", f"as at {as_at}", "", "Assets", f"as at {as_at}", ""])
    for index in range(max(len(liabilities), len(assets))):
        left = liabilities[index] if index < len(liabilities) else ("", None, None)
        right = assets[index] if index < len(assets) else ("", None, None)
        writer.writerow([left[0], left[1] or "", left[2] or "", right[0], right[1] or "", right[2] or ""])
    writer.writerow(["Total", "", left_total, "Total", "", right_total])
    return buffer.getvalue().encode("utf-8")


# Fake ledgers for a multi-file company: bank book, a party ledger, payment
# and receipt registers and an expense ledger. Rows are
# (date, particulars, voucher type, voucher no, debit, credit).
BANK_LEDGER = [
    (datetime(2026, 11, 2), "Sharma Hardware Stores", "Receipt", "R-101", "48250.00", None),
    (datetime(2026, 11, 5), "Office Rent", "Payment", "P-201", None, "18000.00"),
    (datetime(2026, 11, 9), "Gupta Electricals", "Receipt", "R-102", "15600.00", None),
    (datetime(2026, 11, 14), "Demo Freight Movers", "Payment", "P-202", None, "6400.00"),
    (datetime(2026, 11, 22), "Patel Building Supplies", "Receipt", "R-103", "60000.00", None),
]
EXPENSE_LEDGER = [
    (datetime(2026, 11, 5), "Office Rent", "Payment", "P-201", "18000.00", None),
    (datetime(2026, 11, 14), "Freight Charges", "Payment", "P-202", "6400.00", None),
    (datetime(2026, 11, 20), "Electricity Charges", "Journal", "J-31", "3250.00", None),
]
DEBTOR_RECEIPTS = [
    (datetime(2026, 11, 2), "Sharma Hardware Stores", "Receipt", "R-101", None, "48250.00"),
    (datetime(2026, 11, 9), "Gupta Electricals", "Receipt", "R-102", None, "15600.00"),
    (datetime(2026, 11, 22), "Patel Building Supplies", "Receipt", "R-103", None, "60000.00"),
]
CREDITOR_PAYMENTS = [
    (datetime(2026, 11, 6), "Demo Cement Works", "Payment", "P-203", "52000.00", None),
    (datetime(2026, 11, 19), "Sample Steel Traders", "Payment", "P-204", "31500.00", None),
]


def register_xlsx(
    report_name: str,
    rows,
    period: str = "1-Nov-26 to 30-Nov-26",
    *,
    company: str = COMPANY,
) -> bytes:
    """Any Tally voucher register or ledger export with Debit and Credit columns."""

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = report_name[:31]
    header_block = [
        company,
        "Shop No. 12, Sample Market Road",
        "Demo Nagar, Pune - 411000",
        "Contact : 90000 11111",
        "E-Mail : accounts@demotraders.example",
        report_name,
        period,
    ]
    for row, text in enumerate(header_block, start=1):
        sheet.cell(row=row, column=1, value=text)
    for column, text in enumerate(["Date", "Particulars", "Vch Type", "Vch No.", "Debit", "Credit"], start=1):
        sheet.cell(row=8, column=column, value=text)
    for column, text in enumerate(["", "", "", "", "Amount", "Amount"], start=1):
        sheet.cell(row=9, column=column, value=text or None)
    line = 10
    debit_total = credit_total = Decimal(0)
    for when, party, kind, number, debit, credit in rows:
        sheet.cell(row=line, column=1, value=when)
        sheet.cell(row=line, column=2, value=party)
        sheet.cell(row=line, column=3, value=kind)
        sheet.cell(row=line, column=4, value=number)
        if debit:
            sheet.cell(row=line, column=5, value=float(debit))
            debit_total += Decimal(debit)
        if credit:
            sheet.cell(row=line, column=6, value=float(credit))
            credit_total += Decimal(credit)
        line += 1
    sheet.cell(row=line, column=1, value="Total:")
    sheet.cell(row=line, column=5, value=float(debit_total))
    sheet.cell(row=line, column=6, value=float(credit_total))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def main() -> None:
    (HERE / "sales_register_nov26.xlsx").write_bytes(sales_register_xlsx())
    (HERE / "sales_register_dec26.xlsx").write_bytes(
        sales_register_xlsx(DECEMBER_VOUCHERS, "1-Dec-26 to 31-Dec-26")
    )
    (HERE / "balance_sheet_19nov26.csv").write_bytes(balance_sheet_csv())


if __name__ == "__main__":
    main()
