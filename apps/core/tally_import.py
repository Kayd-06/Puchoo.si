"""Turn Tally ERP report exports (TallyPrime / Tally.ERP 9) into clean tables.

A Tally export is a printed report, not a table: a company header block
(name, address, phone, email), a report title and period, one or two header
rows, the detail lines, and Total rows. This module recognises the report,
keeps the header block out of the data, and returns rows that are safe to
query and sum.

Privacy: the company header is kept only as local report metadata (a JSON
sidecar next to the workspace database). It never enters a table, a schema
string or a model prompt. Address, phone and email lines are dropped.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

VOUCHER_REGISTER = "voucher_register"
BALANCE_SHEET = "balance_sheet"
PROFIT_AND_LOSS = "profit_and_loss"
TRIAL_BALANCE = "trial_balance"

# Report title (lower case) -> (report type, table name).
REPORT_TITLES: dict[str, tuple[str, str]] = {
    "sales register": (VOUCHER_REGISTER, "sales_register"),
    "purchase register": (VOUCHER_REGISTER, "purchase_register"),
    "day book": (VOUCHER_REGISTER, "day_book"),
    "journal register": (VOUCHER_REGISTER, "journal_register"),
    "payment register": (VOUCHER_REGISTER, "payment_register"),
    "receipt register": (VOUCHER_REGISTER, "receipt_register"),
    "contra register": (VOUCHER_REGISTER, "contra_register"),
    "credit note register": (VOUCHER_REGISTER, "credit_note_register"),
    "debit note register": (VOUCHER_REGISTER, "debit_note_register"),
    "sales order register": (VOUCHER_REGISTER, "sales_order_register"),
    "purchase order register": (VOUCHER_REGISTER, "purchase_order_register"),
    "ledger vouchers": (VOUCHER_REGISTER, "ledger_vouchers"),
    "balance sheet": (BALANCE_SHEET, "balance_sheet"),
    "profit & loss a/c": (PROFIT_AND_LOSS, "profit_and_loss"),
    "profit and loss a/c": (PROFIT_AND_LOSS, "profit_and_loss"),
    "profit & loss": (PROFIT_AND_LOSS, "profit_and_loss"),
    "profit and loss": (PROFIT_AND_LOSS, "profit_and_loss"),
    "trial balance": (TRIAL_BALANCE, "trial_balance"),
}
TABLE_NAMES = {table for _, table in REPORT_TITLES.values()}

SIDES = {
    BALANCE_SHEET: ("liability", "asset"),
    PROFIT_AND_LOSS: ("expense", "income"),
}

# Fixed wording, not read from data, so it is safe to show the SQL model.
# Standard Tally primary group names are public product vocabulary.
_STANDARD_GROUPS = (
    "Capital Account, Loans (Liability), Current Liabilities, Fixed Assets, Investments, "
    "Current Assets, Profit & Loss A/c, Suspense A/c, Branch / Divisions, Misc. Expenses (ASSET)"
)
VOUCHER_NOTE = (
    "Imported Tally voucher register. One row per voucher. date is ISO YYYY-MM-DD. "
    "party is the ledger or party name. debit_amount and credit_amount are amounts; "
    "Tally's Total row is not stored, so SUM over rows gives the total. "
    "period_start and period_end are the report period. voucher_no is text."
)
STATEMENT_NOTE = (
    "Imported Tally {report} in long format. row_type is 'group' (a group total, e.g. one "
    "of: {groups}) or 'item' (an account inside group_name). Never add groups and their "
    "items together: use row_type = 'group' for group totals and row_type = 'item' for "
    "accounts. side is {sides}. Tally's Total rows are not stored. amount can be negative."
)
TRIAL_BALANCE_NOTE = (
    "Imported Tally trial balance. One row per account or group. debit_amount and "
    "credit_amount are closing balances. Tally's Grand Total row is not stored."
)


def schema_note_for_type(report_type: str) -> str | None:
    """Model-facing note for a Tally report type (fixed text, no data)."""

    if report_type == VOUCHER_REGISTER:
        return VOUCHER_NOTE
    if report_type == TRIAL_BALANCE:
        return TRIAL_BALANCE_NOTE
    if report_type in SIDES:
        left, right = SIDES[report_type]
        report = "balance sheet" if report_type == BALANCE_SHEET else "profit and loss account"
        return STATEMENT_NOTE.format(report=report, groups=_STANDARD_GROUPS, sides=f"'{left}' or '{right}'")
    return None


def schema_note(table: str) -> str | None:
    """Model-facing note for an importer-owned table name (fixed text, no data)."""

    for report_type, name in REPORT_TITLES.values():
        if name == table:
            return schema_note_for_type(report_type)
    return None


class TallyImportError(ValueError):
    """A user-safe message about why a Tally report could not be imported."""


@dataclass
class TotalsCheck:
    label: str
    expected: float | None
    actual: float | None
    ok: bool


@dataclass
class ReportMetadata:
    company_name: str | None
    report_name: str
    period_start: str | None
    period_end: str | None
    report_date: str | None


@dataclass
class TallyReport:
    report_type: str
    table: str
    columns: list[str]
    rows: list[dict[str, Any]]
    metadata: ReportMetadata
    checks: list[TotalsCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def totals_status(self) -> str:
        if not self.checks:
            return "unavailable"
        return "pass" if all(check.ok for check in self.checks) else "fail"

    def preview(self, sample_size: int = 8) -> dict[str, Any]:
        return {
            "kind": "tally",
            "report_type": self.report_type,
            "report_name": self.metadata.report_name,
            "company_name": self.metadata.company_name,
            "period_start": self.metadata.period_start,
            "period_end": self.metadata.period_end,
            "report_date": self.metadata.report_date,
            "table": self.table,
            "columns": list(self.columns),
            "row_count": len(self.rows),
            "sample_rows": [dict(row) for row in self.rows[:sample_size]],
            "totals_check": {
                "status": self.totals_status,
                "checks": [asdict(check) for check in self.checks],
            },
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------
# Value parsing
# --------------------------------------------------------------------------

_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DATE_TEXT = r"\d{1,2}-[A-Za-z]{3}-\d{2,4}"
_PERIOD = re.compile(rf"({_DATE_TEXT})\s+to\s+({_DATE_TEXT})", re.IGNORECASE)
_AS_AT = re.compile(rf"as\s+at\s+({_DATE_TEXT})", re.IGNORECASE)
_TWO_DECIMALS = Decimal("0.01")


def cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_date(value: object) -> date | None:
    """Parse Tally dates: real Excel dates, '1-Nov-26', '01-11-2026', ISO."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    match = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{2}|\d{4})", text)
    if match:
        day, month, year = match.groups()
        if month.lower() not in _MONTHS:
            return None
        full_year = int(year) + 2000 if len(year) == 2 else int(year)
        try:
            return date(full_year, _MONTHS.index(month.lower()) + 1, int(day))
        except ValueError:
            return None
    for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def parse_amount(value: object) -> Decimal | None:
    """Parse '1,23,456.00', '-792625.00', '(1,234.00)', '5,000.00 Dr', '300 Cr'.

    Dr is positive and Cr is negative, the usual sign convention for Tally
    balances. Returns None for blank or non-numeric text.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text:
        return None
    sign = 1
    suffix = re.fullmatch(r"(.*?[\d)])\s*(dr|cr)\.?", text, re.IGNORECASE)
    if suffix:
        text = suffix.group(1).strip()
        if suffix.group(2).lower() == "cr":
            sign = -1
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
        sign = -sign
    text = re.sub(r"^(₹|rs\.?|inr)\s*", "", text, flags=re.IGNORECASE)
    if text.startswith("-"):
        sign = -sign
        text = text[1:].strip()
    elif text.startswith("+"):
        text = text[1:].strip()
    if not re.fullmatch(r"\d{1,3}(,\d{2,3})*(\.\d+)?|\d+(\.\d+)?", text):
        return None
    try:
        return sign * Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _money(value: Decimal | None) -> float | None:
    return None if value is None else float(value.quantize(_TWO_DECIMALS))


def _snake(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text.strip().lower()).strip("_")
    if not cleaned:
        return ""
    return f"_{cleaned}" if cleaned[0].isdigit() else cleaned[:63]


def _non_empty(row: list[object]) -> list[str]:
    return [cell_text(value) for value in row if cell_text(value)]


def _is_blank(row: list[object]) -> bool:
    return not _non_empty(row)


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

@dataclass
class _Layout:
    report_type: str
    table: str
    report_name: str
    title_row: int
    header_row: int
    company_name: str | None
    period_start: date | None
    period_end: date | None


_SCAN_ROWS = 20


def _title_of(row: list[object]) -> str | None:
    values = _non_empty(row)
    if len(values) != 1:
        return None
    text = re.sub(r"\s+", " ", values[0]).strip().lower()
    return text if text in REPORT_TITLES else None


def _header_row(grid: list[list[object]], start: int, report_type: str) -> int | None:
    for index in range(start, min(len(grid), start + 8)):
        cells = [cell_text(value).lower() for value in grid[index]]
        if report_type == VOUCHER_REGISTER:
            if "date" in cells and "particulars" in cells:
                return index
        elif report_type == TRIAL_BALANCE:
            if "particulars" in cells:
                return index
        else:
            left, right = ("liabilities", "assets") if report_type == BALANCE_SHEET else ("particulars", "particulars")
            labels = [cell for cell in cells if cell and not cell.startswith("as at") and not _PERIOD.search(cell)]
            if report_type == BALANCE_SHEET and left in cells and right in cells:
                return index
            if report_type == PROFIT_AND_LOSS and labels.count("particulars") >= 2:
                return index
    return None


def detect_layout(grid: list[list[object]]) -> _Layout | None:
    """Find the Tally report title near the top and the header row below it."""

    for index, row in enumerate(grid[:_SCAN_ROWS]):
        title = _title_of(row)
        if title is None:
            continue
        report_type, table = REPORT_TITLES[title]
        header = _header_row(grid, index + 1, report_type)
        if header is None:
            continue
        company = None
        for above in grid[:index]:
            values = _non_empty(above)
            if values and values[0].lower() != "table 1":
                company = values[0]
                break
        period_start = period_end = None
        for between in grid[index + 1 : header + 1]:
            for value in _non_empty(between):
                match = _PERIOD.search(value)
                if match:
                    period_start, period_end = parse_date(match.group(1)), parse_date(match.group(2))
                    break
            if period_start:
                break
        return _Layout(
            report_type=report_type,
            table=table,
            report_name=_non_empty(row)[0].strip(),
            title_row=index,
            header_row=header,
            company_name=company,
            period_start=period_start,
            period_end=period_end,
        )
    return None


# --------------------------------------------------------------------------
# Voucher registers (Sales / Purchase Register, Day Book, ledger vouchers)
# --------------------------------------------------------------------------

_KNOWN_VOUCHER_COLUMNS = {
    "date": "date",
    "particulars": "party",
    "vch_type": "voucher_type",
    "voucher_type": "voucher_type",
    "vch_no": "voucher_no",
    "voucher_no": "voucher_no",
    "debit": "debit_amount",
    "debit_amount": "debit_amount",
    "credit": "credit_amount",
    "credit_amount": "credit_amount",
}
_TEXT_COLUMNS = {"date", "party", "voucher_type", "voucher_no"}
_SKIP_LINE = re.compile(r"^(grand\s+total|total|sub\s*-?\s*total|current\s+total|opening\s+balance|closing\s+balance)\b", re.I)


def _combined_headers(grid: list[list[object]], header: int) -> tuple[list[str], int]:
    first = [cell_text(value) for value in grid[header]]
    data_start = header + 1
    second: list[str] = []
    if data_start < len(grid):
        candidate = [cell_text(value) for value in grid[data_start]]
        # A second header row has an empty first cell and only labels such as
        # "Amount": no date and no numbers.
        labels = [value for value in candidate if value]
        if labels and not (candidate[0] if candidate else "") and all(parse_amount(value) is None for value in labels):
            second = candidate
            data_start += 1
    width = max(len(first), len(second))
    names: list[str] = []
    used: set[str] = set()
    for column in range(width):
        top = first[column] if column < len(first) else ""
        below = second[column] if column < len(second) else ""
        raw = _snake(" ".join(part for part in (top, below) if part))
        name = _KNOWN_VOUCHER_COLUMNS.get(raw) or _KNOWN_VOUCHER_COLUMNS.get(_snake(top)) or raw or f"column_{column + 1}"
        base, suffix = name, 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        names.append(name)
    return names, data_start


def _parse_voucher_register(grid: list[list[object]], layout: _Layout) -> TallyReport:
    names, data_start = _combined_headers(grid, layout.header_row)
    for required in ("date", "party"):
        if required not in names:
            raise TallyImportError("This Tally register has no Date or Particulars column.")
    amount_columns = [name for name in names if name not in _TEXT_COLUMNS and name.endswith("amount")]
    rows: list[dict[str, Any]] = []
    totals: dict[str, Decimal] = {}
    sums: dict[str, Decimal] = {name: Decimal(0) for name in amount_columns}
    warnings: list[str] = []
    skipped_detail = 0
    skipped_balances = 0
    last_date: date | None = None
    period_start = layout.period_start.isoformat() if layout.period_start else None
    period_end = layout.period_end.isoformat() if layout.period_end else None

    for raw in grid[data_start:]:
        if _is_blank(raw):
            continue
        values = {name: (raw[index] if index < len(raw) else None) for index, name in enumerate(names)}
        first_text = cell_text(raw[0] if raw else None)
        party_text = cell_text(values.get("party"))
        if _SKIP_LINE.match(first_text) or (not first_text and _SKIP_LINE.match(party_text)):
            label = (first_text or party_text).lower()
            if label.startswith(("total", "grand total")):
                for name in amount_columns:
                    amount = parse_amount(values.get(name))
                    if amount is not None:
                        totals[name] = amount
            else:
                skipped_balances += 1
            continue
        row_date = parse_date(values.get("date"))
        voucher_no = cell_text(values.get("voucher_no"))
        if row_date is None:
            if voucher_no and last_date is not None:
                row_date = last_date  # Tally prints the date once per day in some views.
            else:
                skipped_detail += 1
                continue
        last_date = row_date
        record: dict[str, Any] = {}
        for name in names:
            value = values.get(name)
            if name == "date":
                record[name] = row_date.isoformat()
            elif name in amount_columns:
                amount = parse_amount(value)
                record[name] = _money(amount)
                if amount is not None:
                    sums[name] += amount
            elif name == "voucher_no":
                record[name] = voucher_no or None
            else:
                record[name] = cell_text(value) or None
        record["period_start"] = period_start
        record["period_end"] = period_end
        record["source_report"] = layout.report_name
        rows.append(record)

    if not rows:
        raise TallyImportError("No voucher lines were found under the report header.")
    checks = [
        TotalsCheck(
            label=f"{name.replace('_', ' ').capitalize()}: sum of rows vs Tally total",
            expected=_money(totals[name]),
            actual=_money(sums[name]),
            ok=abs(totals[name] - sums[name]) < _TWO_DECIMALS,
        )
        for name in amount_columns
        if name in totals
    ]
    if not totals:
        warnings.append("No Total row was found, so the totals could not be checked against Tally.")
    for check in checks:
        if not check.ok:
            warnings.append(
                f"{check.label}: rows add up to {check.actual:,.2f} but Tally shows {check.expected:,.2f}."
            )
    if skipped_detail:
        warnings.append(f"{skipped_detail} line(s) without a date or voucher number were left out (detail or narration lines).")
    if skipped_balances:
        warnings.append(f"{skipped_balances} opening/closing balance line(s) were left out of the table.")
    keys = [(row.get("voucher_type"), row.get("voucher_no"), row.get("date")) for row in rows if row.get("voucher_no")]
    duplicates = len(keys) - len(set(keys))
    if duplicates:
        warnings.append(f"{duplicates} voucher(s) appear more than once in this file (same type, number and date).")
    columns = [*names, "period_start", "period_end", "source_report"]
    return TallyReport(
        report_type=VOUCHER_REGISTER,
        table=layout.table,
        columns=columns,
        rows=rows,
        metadata=ReportMetadata(
            company_name=layout.company_name,
            report_name=layout.report_name,
            period_start=period_start,
            period_end=period_end,
            report_date=period_end,
        ),
        checks=checks,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Balance Sheet / Profit & Loss (two sides printed next to each other)
# --------------------------------------------------------------------------

_FLAGGED_ACCOUNTS = {
    "suspense a/c": "Suspense A/c is present. Entries there are unclassified and should be reviewed in Tally.",
    "difference in opening balances": (
        "Tally shows a Difference in opening balances{amount}. Opening balances do not match in Tally, "
        "so check them before relying on these totals."
    ),
}
_CARRY = re.compile(r"\b(c/o|b/f|c/f|b/d|c/d)\b", re.I)


def _statement_columns() -> list[str]:
    return [
        "report_date", "period_start", "period_end", "side", "row_type",
        "group_name", "account", "amount", "line_no", "source_report",
    ]


def _parse_side_by_side(grid: list[list[object]], layout: _Layout) -> TallyReport:
    header = [cell_text(value) for value in grid[layout.header_row]]
    report_date = None
    for value in header:
        match = _AS_AT.search(value)
        if match:
            report_date = parse_date(match.group(1))
            break
    label_columns = [
        index for index, value in enumerate(header)
        if value and not value.lower().startswith("as at") and not _PERIOD.search(value) and parse_date(value) is None
    ]
    if len(label_columns) < 2:
        raise TallyImportError("Could not find the two sides of this Tally statement.")
    left_start, right_start = label_columns[0], label_columns[1]
    blocks = [(left_start, right_start), (right_start, max(right_start + 3, max(len(row) for row in grid)))]
    side_names = SIDES[layout.report_type]
    period_start = layout.period_start.isoformat() if layout.period_start else None
    period_end = layout.period_end.isoformat() if layout.period_end else None
    as_at = (report_date or layout.period_end)
    as_at_text = as_at.isoformat() if as_at else None

    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    checks: list[TotalsCheck] = []
    flagged: dict[str, str] = {}
    totals: dict[str, Decimal] = {}

    for side_index, (start, end) in enumerate(blocks):
        side = side_names[side_index]
        current_group: str | None = None
        group_amount: Decimal | None = None
        group_items: list[Decimal] = []
        section_groups: list[Decimal] = []

        def close_group() -> None:
            nonlocal current_group, group_amount, group_items
            if current_group and group_items and group_amount is not None:
                item_sum = sum(group_items, Decimal(0))
                ok = abs(item_sum - group_amount) < _TWO_DECIMALS
                checks.append(TotalsCheck(
                    label=f"{side.capitalize()} group '{current_group}': items vs group amount",
                    expected=_money(group_amount), actual=_money(item_sum), ok=ok,
                ))
                if not ok:
                    warnings.append(
                        f"Items under {current_group} add up to {item_sum:,.2f} but Tally shows {group_amount:,.2f}."
                    )
            current_group, group_amount, group_items = None, None, []

        for line_no, raw in enumerate(grid[layout.header_row + 1 :], start=1):
            cells = list(raw[start:end]) + [None] * 3
            name = cell_text(cells[0])
            amounts = [parse_amount(value) for value in cells[1 : end - start]]
            if end - start >= 3:
                item_amount, group_value = amounts[0], amounts[1]
            else:
                item_amount, group_value = None, amounts[0] if amounts else None
            if not name and item_amount is None and group_value is None:
                continue
            if name.lower().startswith(("total", "grand total")):
                close_group()
                total = group_value if group_value is not None else item_amount
                if total is not None:
                    section_sum = sum(section_groups, Decimal(0))
                    ok = abs(section_sum - total) < _TWO_DECIMALS
                    checks.append(TotalsCheck(
                        label=f"{side.capitalize()} side: groups vs Tally total",
                        expected=_money(total), actual=_money(section_sum), ok=ok,
                    ))
                    if not ok:
                        warnings.append(
                            f"The {side} groups add up to {section_sum:,.2f} but Tally's total is {total:,.2f}."
                        )
                    totals.setdefault(side, total)
                section_groups = []
                continue
            lowered = name.lower()
            if lowered in _FLAGGED_ACCOUNTS:
                amount = group_value if group_value is not None else item_amount
                text = _FLAGGED_ACCOUNTS[lowered]
                flagged[lowered] = text.format(amount=f" of {amount:,.2f}" if amount is not None else "")
            if group_value is None and current_group and not _CARRY.search(name):
                # Tally prints zero-balance ledgers and sub-groups (for example
                # "Loans & Advances (Asset)" or "Suspense A/c") with both amount
                # columns empty in the middle of a group. They stay inside the
                # current group so the lines after them keep the right parent.
                if item_amount is not None:
                    group_items.append(item_amount)
                rows.append({
                    "report_date": as_at_text, "period_start": period_start, "period_end": period_end,
                    "side": side, "row_type": "item", "group_name": current_group, "account": name or None,
                    "amount": _money(item_amount) if item_amount is not None else None,
                    "line_no": line_no, "source_report": layout.report_name,
                })
                continue
            close_group()
            amount = group_value if group_value is not None else item_amount
            row_type = "carry" if _CARRY.search(name) else "group"
            rows.append({
                "report_date": as_at_text, "period_start": period_start, "period_end": period_end,
                "side": side, "row_type": row_type, "group_name": None, "account": name or None,
                "amount": _money(amount), "line_no": line_no, "source_report": layout.report_name,
            })
            if amount is not None:
                section_groups.append(amount)
            current_group, group_amount, group_items = name, amount, []
        close_group()

    warnings.extend(flagged.values())
    if len(totals) == 2:
        left_total, right_total = totals[side_names[0]], totals[side_names[1]]
        ok = abs(left_total - right_total) < _TWO_DECIMALS
        checks.append(TotalsCheck(
            label=f"{side_names[0].capitalize()} total equals {side_names[1]} total",
            expected=_money(left_total), actual=_money(right_total), ok=ok,
        ))
        if not ok:
            warnings.append(
                f"Tally's {side_names[0]} total ({left_total:,.2f}) does not equal the {side_names[1]} total ({right_total:,.2f})."
            )
    else:
        warnings.append("Tally's Total row was not found on both sides, so the sides could not be compared.")
    if not rows:
        raise TallyImportError("No account lines were found under the report header.")
    return TallyReport(
        report_type=layout.report_type,
        table=layout.table,
        columns=_statement_columns(),
        rows=rows,
        metadata=ReportMetadata(
            company_name=layout.company_name,
            report_name=layout.report_name,
            period_start=period_start,
            period_end=period_end,
            report_date=as_at_text,
        ),
        checks=checks,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Trial Balance (Particulars, Debit, Credit)
# --------------------------------------------------------------------------

def _parse_trial_balance(grid: list[list[object]], layout: _Layout) -> TallyReport:
    names, data_start = _combined_headers(grid, layout.header_row)
    debit = next((i for i, name in enumerate(names) if "debit" in name), None)
    credit = next((i for i, name in enumerate(names) if "credit" in name), None)
    if debit is None or credit is None:
        raise TallyImportError("This trial balance has no Debit and Credit columns.")
    period_start = layout.period_start.isoformat() if layout.period_start else None
    period_end = layout.period_end.isoformat() if layout.period_end else None
    rows: list[dict[str, Any]] = []
    sums = [Decimal(0), Decimal(0)]
    totals: list[Decimal | None] = [None, None]
    warnings: list[str] = []
    for line_no, raw in enumerate(grid[data_start:], start=1):
        if _is_blank(raw):
            continue
        name = cell_text(raw[0] if raw else None)
        dr = parse_amount(raw[debit] if debit < len(raw) else None)
        cr = parse_amount(raw[credit] if credit < len(raw) else None)
        if name.lower().startswith(("total", "grand total")):
            totals = [dr, cr]
            continue
        if name.lower() in _FLAGGED_ACCOUNTS:
            amount = dr if dr is not None else cr
            warnings.append(_FLAGGED_ACCOUNTS[name.lower()].format(amount=f" of {amount:,.2f}" if amount is not None else ""))
        sums[0] += dr or 0
        sums[1] += cr or 0
        rows.append({
            "report_date": period_end, "period_start": period_start, "period_end": period_end,
            "account": name or None, "debit_amount": _money(dr), "credit_amount": _money(cr),
            "line_no": line_no, "source_report": layout.report_name,
        })
    checks: list[TotalsCheck] = []
    for label, index in (("Debit", 0), ("Credit", 1)):
        if totals[index] is not None:
            ok = abs(sums[index] - totals[index]) < _TWO_DECIMALS
            checks.append(TotalsCheck(f"{label}: sum of rows vs Tally total", _money(totals[index]), _money(sums[index]), ok))
            if not ok:
                warnings.append(f"{label} rows add up to {sums[index]:,.2f} but Tally shows {totals[index]:,.2f}.")
    if totals[0] is not None and totals[1] is not None:
        ok = abs(totals[0] - totals[1]) < _TWO_DECIMALS
        checks.append(TotalsCheck("Debit total equals credit total", _money(totals[0]), _money(totals[1]), ok))
    if not rows:
        raise TallyImportError("No account lines were found under the report header.")
    return TallyReport(
        report_type=TRIAL_BALANCE,
        table=layout.table,
        columns=["report_date", "period_start", "period_end", "account", "debit_amount", "credit_amount", "line_no", "source_report"],
        rows=rows,
        metadata=ReportMetadata(layout.company_name, layout.report_name, period_start, period_end, period_end),
        checks=checks,
        warnings=warnings,
    )


def parse_tally_report(grid: list[list[object]]) -> TallyReport | None:
    """Return a cleaned report, or None when the grid is not a known Tally report."""

    layout = detect_layout(grid)
    if layout is None:
        return None
    if layout.report_type == VOUCHER_REGISTER:
        return _parse_voucher_register(grid, layout)
    if layout.report_type == TRIAL_BALANCE:
        return _parse_trial_balance(grid, layout)
    return _parse_side_by_side(grid, layout)


# --------------------------------------------------------------------------
# Unknown layouts: let the user pick the header row
# --------------------------------------------------------------------------

PREVIEW_ROWS = 15


def looks_like_report_layout(grid: list[list[object]]) -> bool:
    """True when the first row is not a usable header (title lines above the table)."""

    rows = [row for row in grid[:PREVIEW_ROWS] if not _is_blank(row)]
    if len(rows) < 2:
        return False
    width = max(len(_non_empty(row)) for row in rows)
    first = len(_non_empty(grid[0])) if grid else 0
    return width >= 2 and (first == 0 or first * 2 <= width)


def suggest_header_row(grid: list[list[object]]) -> int:
    rows = grid[:PREVIEW_ROWS]
    width = max((len(_non_empty(row)) for row in rows), default=0)
    for index, row in enumerate(rows):
        values = _non_empty(row)
        if width and len(values) * 2 > width and all(parse_amount(value) is None for value in values):
            return index
    return 0


def preview_rows(grid: list[list[object]], limit: int = PREVIEW_ROWS) -> list[list[str]]:
    width = max((len(row) for row in grid[:limit]), default=0)
    return [[cell_text(value)[:60] for value in (list(row) + [None] * width)[:width]] for row in grid[:limit]]


def table_from_header_row(grid: list[list[object]], header_row: int) -> tuple[list[str], list[list[object]]]:
    """Use the chosen row as column names, drop everything above and blank lines."""

    if header_row < 0 or header_row >= len(grid):
        raise TallyImportError("Choose a header row that exists in the file.")
    header = [cell_text(value) for value in grid[header_row]]
    body = [list(row) for row in grid[header_row + 1 :] if not _is_blank(row)]
    width = max([len(header), *(len(row) for row in body)]) if body else len(header)
    keep = [index for index in range(width) if (index < len(header) and header[index]) or any(
        index < len(row) and cell_text(row[index]) for row in body
    )]
    if not keep:
        raise TallyImportError("The chosen header row has no columns.")
    names = [header[index] if index < len(header) and header[index] else f"column_{index + 1}" for index in keep]
    rows = [[row[index] if index < len(row) else None for index in keep] for row in body]
    return names, rows


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

_SQL_TYPES = {"amount": "REAL", "line_no": "INTEGER"}


def _column_type(report: TallyReport, column: str) -> str:
    if column.endswith("amount"):
        return "REAL"
    return _SQL_TYPES.get(column, "TEXT")


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _voucher_key(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (row.get("voucher_type"), row.get("voucher_no"), row.get("date"))


@dataclass
class SaveResult:
    table: str
    rows_added: int
    rows_skipped: int
    rows_replaced: int = 0


def save_report(report: TallyReport, database_file: Path) -> SaveResult:
    """Create or extend the report table in a workspace SQLite file.

    Voucher registers append only vouchers that are not already stored (same
    voucher type, number and date), so re-uploading an overlapping period does
    not double count. A statement for a date that is already stored replaces
    that snapshot instead of adding a second copy.
    """

    connection = sqlite3.connect(database_file)
    try:
        existing_columns = [row[1] for row in connection.execute(f"PRAGMA table_info({_quote(report.table)})")]
        if existing_columns:
            missing = [column for column in report.columns if column not in existing_columns]
            for column in missing:
                connection.execute(
                    f"ALTER TABLE {_quote(report.table)} ADD COLUMN {_quote(column)} {_column_type(report, column)}"
                )
        else:
            definition = ", ".join(f"{_quote(column)} {_column_type(report, column)}" for column in report.columns)
            connection.execute(f"CREATE TABLE {_quote(report.table)} ({definition})")
        replaced = 0
        to_insert = report.rows
        skipped = 0
        if report.report_type == VOUCHER_REGISTER and existing_columns:
            stored = {
                tuple(row)
                for row in connection.execute(
                    f"SELECT voucher_type, voucher_no, date FROM {_quote(report.table)} WHERE voucher_no IS NOT NULL"
                )
            } if {"voucher_type", "voucher_no", "date"} <= set(existing_columns) else set()
            to_insert = [row for row in report.rows if not row.get("voucher_no") or _voucher_key(row) not in stored]
            skipped = len(report.rows) - len(to_insert)
        elif report.report_type != VOUCHER_REGISTER and existing_columns and report.metadata.report_date:
            replaced = connection.execute(
                f"DELETE FROM {_quote(report.table)} WHERE report_date = ?", (report.metadata.report_date,)
            ).rowcount
        if to_insert:
            columns = report.columns
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"INSERT INTO {_quote(report.table)} ({', '.join(_quote(c) for c in columns)}) VALUES ({placeholders})",
                [tuple(row.get(column) for column in columns) for row in to_insert],
            )
        connection.commit()
    finally:
        connection.close()
    return SaveResult(report.table, len(to_insert), skipped, replaced)


def existing_voucher_overlap(report: TallyReport, database_file: Path) -> int:
    """How many vouchers in this report are already stored (for the preview)."""

    if report.report_type != VOUCHER_REGISTER or not database_file.is_file():
        return 0
    connection = sqlite3.connect(f"file:{database_file}?mode=ro", uri=True)
    try:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({_quote(report.table)})")}
        if not {"voucher_type", "voucher_no", "date"} <= columns:
            return 0
        stored = {tuple(row) for row in connection.execute(
            f"SELECT voucher_type, voucher_no, date FROM {_quote(report.table)} WHERE voucher_no IS NOT NULL")}
    finally:
        connection.close()
    return sum(1 for row in report.rows if row.get("voucher_no") and _voucher_key(row) in stored)


def write_metadata(metadata_file: Path, report: TallyReport, result: SaveResult) -> dict[str, Any]:
    """Record report details next to the database. Local only; never sent to a model."""

    try:
        current = json.loads(metadata_file.read_text(encoding="utf-8")) if metadata_file.is_file() else {}
    except (OSError, ValueError):
        current = {}
    reports = current.get("reports") if isinstance(current.get("reports"), list) else []
    entry = {
        **asdict(report.metadata),
        "report_type": report.report_type,
        "table": report.table,
        "rows_added": result.rows_added,
        "rows_skipped": result.rows_skipped,
        "rows_replaced": result.rows_replaced,
        "totals_status": report.totals_status,
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = {"kind": "tally", "reports": [*reports, entry]}
    metadata_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def read_metadata(metadata_file: Path) -> dict[str, Any]:
    try:
        payload = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def companies_in(metadata: dict[str, Any]) -> set[str]:
    reports: Iterable[dict[str, Any]] = metadata.get("reports") or []
    return {str(item.get("company_name")) for item in reports if isinstance(item, dict) and item.get("company_name")}


__all__ = [
    "BALANCE_SHEET",
    "PROFIT_AND_LOSS",
    "TABLE_NAMES",
    "TRIAL_BALANCE",
    "VOUCHER_REGISTER",
    "SaveResult",
    "TallyImportError",
    "TallyReport",
    "companies_in",
    "detect_layout",
    "existing_voucher_overlap",
    "looks_like_report_layout",
    "parse_amount",
    "parse_date",
    "parse_tally_report",
    "preview_rows",
    "read_metadata",
    "save_report",
    "schema_note",
    "schema_note_for_type",
    "suggest_header_row",
    "table_from_header_row",
    "write_metadata",
]
