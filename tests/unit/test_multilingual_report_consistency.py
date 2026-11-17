"""Equivalent translated report requests must preserve facts and time boundaries."""
import sqlite3
from types import SimpleNamespace

import pytest

from apps.core.consistent_reports import report_assumptions, report_sql
from apps.core.llm_client import canonicalize_analytics_question, schema_guided_fallback_sql
from apps.core.sarvam_client import SarvamClient

SCHEMA = """Table: products
Columns: product_id (INTEGER), category (TEXT)

Table: invoice_items
Columns: invoice_id (INTEGER), product_id (INTEGER), line_total (REAL)

Table: invoices
Columns: invoice_id (INTEGER), invoice_date (TEXT), total_amount (REAL)

Table: expenses
Columns: expense_id (INTEGER), expense_date (TEXT), amount (REAL)"""

REVENUE = [
    'Which product categories generated the most revenue in the last six months?',
    'Which product categories generated the highest revenue over the past 6 months?',
    'Show product categories by total revenue in the last six months.',
]
EXPENSES = [
    'For each month in the last year, what was the total recorded expense?',
    'What were the total recorded expenses for each month in the past year?',
    'Show monthly total expenses for the last 12 months.',
]

@pytest.mark.parametrize('wordings', [REVENUE, EXPENSES])
def test_equivalent_report_wordings_use_identical_sql(wordings):
    queries = [report_sql(SCHEMA, canonicalize_analytics_question(q)) for q in wordings]
    assert all(queries), 'Reported questions must have schema-grounded report SQL'
    assert len(set(queries)) == 1


def test_report_results_exclude_old_and_future_data_and_avoid_invoice_fanout():
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE products(product_id INTEGER, category TEXT);
        CREATE TABLE invoice_items(invoice_id INTEGER, product_id INTEGER, line_total REAL);
        CREATE TABLE invoices(invoice_id INTEGER, invoice_date TEXT, total_amount REAL);
        CREATE TABLE expenses(expense_id INTEGER, expense_date TEXT, amount REAL);
        INSERT INTO products VALUES (1,'A'),(2,'B');
        INSERT INTO invoices VALUES (1,date('now'),999),(2,'2000-01-01',999),(3,'2999-01-01',999);
        INSERT INTO invoice_items VALUES (1,1,10),(1,1,20),(1,2,40),(2,1,500),(3,1,900);
        INSERT INTO expenses VALUES (1,date('now'),10),(2,date('now'),25),(3,'2000-01-01',500),(4,'2999-01-01',900);
    ''')
    try:
        revenue = report_sql(SCHEMA, canonicalize_analytics_question(REVENUE[0]))
        expenses = report_sql(SCHEMA, canonicalize_analytics_question(EXPENSES[0]))
        assert revenue and expenses
        assert db.execute(revenue).fetchall() == [('B', 40.0), ('A', 30.0)]
        rows = db.execute(expenses).fetchall()
        assert len(rows) == 1 and rows[0][1] == 35.0
    finally:
        db.close()


@pytest.mark.parametrize('question', [
    REVENUE[0].rstrip('?') + ' for region North?',
    EXPENSES[0].rstrip('?') + ' excluding travel?',
    'Which product categories generated the most revenue in the last six months of 2024?',
    'Show monthly total expenses for the last calendar year.',
])
def test_extra_filters_and_explicit_calendar_periods_are_not_rewritten(question):
    assert canonicalize_analytics_question(question) == question
    assert report_sql(SCHEMA, question) is None
    assert schema_guided_fallback_sql(SCHEMA, question) is None


def test_missing_line_revenue_does_not_substitute_repeated_invoice_totals():
    schema = SCHEMA.replace('line_total (REAL)', 'quantity (INTEGER)')
    assert report_sql(schema, canonicalize_analytics_question(REVENUE[0])) is None


def test_sarvam_sdk_errors_are_normalized_for_safe_router_handling():
    from sarvamai.core.api_error import ApiError
    def fail(**kwargs):
        raise ApiError(status_code=429, body='private provider diagnostics')
    client = SarvamClient(client=SimpleNamespace(text=SimpleNamespace(translate=fail)))
    with pytest.raises(RuntimeError, match='Translation service is unavailable'):
        client.translate('Hello', 'hi-IN')


@pytest.mark.parametrize('dialect,formatter', [('postgresql', 'TO_CHAR('), ('mysql', 'DATE_FORMAT(')])
def test_report_sql_uses_workspace_dialect_and_literal_date_bounds(dialect, formatter):
    from apps.core.consistent_reports import report_sql
    sql = report_sql(SCHEMA, EXPENSES[0], dialect=dialect)
    assert formatter in sql
    assert 'strftime(' not in sql and "'-12 months'" not in sql
    assert 'CAST(e.expense_date AS DATE)' in sql


def test_rolling_revenue_boundaries_clamp_month_end_and_include_full_reference_day(monkeypatch):
    from datetime import date
    from apps.core import consistent_reports
    monkeypatch.setattr(consistent_reports, 'reference_date', lambda: date(2026, 8, 31))
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE products(product_id INTEGER, category TEXT);
        CREATE TABLE invoice_items(invoice_id INTEGER, product_id INTEGER, line_total REAL);
        CREATE TABLE invoices(invoice_id INTEGER, invoice_date TEXT, total_amount REAL);
        INSERT INTO products VALUES (1,'A');
        INSERT INTO invoices VALUES (1,'2026-02-27',900),(2,'2026-02-28',10),
          (3,'2026-08-31 23:59:59',20),(4,'2026-09-01',800);
        INSERT INTO invoice_items VALUES (1,1,900),(2,1,10),(3,1,20),(4,1,800);
    ''')
    try:
        sql = consistent_reports.report_sql(SCHEMA, REVENUE[0])
        assert db.execute(sql).fetchall() == [('A', 30.0)]
    finally:
        db.close()


def test_ambiguous_fact_tables_do_not_get_arbitrarily_selected():
    from apps.core.consistent_reports import report_sql
    schema = SCHEMA + '\n\nTable: archived_expenses\nColumns: expense_id (INTEGER), expense_date (TEXT), amount (REAL)'
    assert report_sql(schema, EXPENSES[0]) is None


def test_schema_guided_fallback_defers_canonical_reports_to_dialect_aware_sql():
    question = canonicalize_analytics_question(EXPENSES[0])
    assert question and report_sql(SCHEMA, question)
    assert schema_guided_fallback_sql(SCHEMA, question) is None


def test_monthly_expenses_cover_exactly_twelve_calendar_months(monkeypatch):
    from datetime import date
    from apps.core import consistent_reports
    monkeypatch.setattr(consistent_reports, 'reference_date', lambda: date(2026, 11, 18))
    db = sqlite3.connect(':memory:')
    db.executescript("""
        CREATE TABLE expenses(expense_id INTEGER, expense_date TEXT, amount REAL);
        INSERT INTO expenses VALUES
          (1,'2025-11-30',100),
          (2,'2025-12-01',10),
          (3,'2026-01-15',20),
          (4,'2026-02-01',30),
          (5,'2026-03-31',40),
          (6,'2026-04-01',50),
          (7,'2026-05-20',60),
          (8,'2026-06-30',70),
          (9,'2026-07-01',80),
          (10,'2026-08-15',90),
          (11,'2026-09-01',100),
          (12,'2026-10-31',110),
          (13,'2026-11-18',120),
          (14,'2026-11-19',200);
    """)
    try:
        sql = report_sql(SCHEMA, EXPENSES[0])
        rows = db.execute(sql).fetchall()
        assert [row[0] for row in rows] == [
            '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05',
            '2026-06', '2026-07', '2026-08', '2026-09', '2026-10', '2026-11',
        ]
        assert len(rows) == 12
        assert rows[0] == ('2025-12', 10.0)
        assert rows[-1] == ('2026-11', 120.0)
        assumptions = report_assumptions(EXPENSES[0])
        assert '2025-12-01' in assumptions[0]
        assert '2026-11-18' in assumptions[0]
        assert 'exactly 12 calendar months' in assumptions[1]
    finally:
        db.close()


@pytest.mark.parametrize('dialect,formatter', [
    ('sqlite', "strftime('%Y-%m', e.expense_date)"),
    ('postgres', "TO_CHAR(CAST(e.expense_date AS TIMESTAMP), 'YYYY-MM')"),
    ('postgresql', "TO_CHAR(CAST(e.expense_date AS TIMESTAMP), 'YYYY-MM')"),
    ('mysql', "DATE_FORMAT(e.expense_date, '%Y-%m')"),
])
def test_monthly_expense_sql_uses_full_calendar_month_bounds_for_each_dialect(monkeypatch, dialect, formatter):
    from datetime import date
    from apps.core import consistent_reports
    monkeypatch.setattr(consistent_reports, 'reference_date', lambda: date(2026, 11, 18))
    sql = report_sql(SCHEMA, EXPENSES[0], dialect=dialect)
    assert sql is not None
    assert formatter in sql
    assert ">= '2025-12-01'" in sql
    assert "< '2026-11-19'" in sql
    assert "'-12 months'" not in sql
    if dialect != 'sqlite':
        assert 'strftime(' not in sql
        assert 'CAST(e.expense_date AS DATE)' in sql
