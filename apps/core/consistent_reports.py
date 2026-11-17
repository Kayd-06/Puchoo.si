"""Closed, schema-checked report intents shared by translated question variants."""
import re
from calendar import monthrange
from datetime import date, timedelta

from apps.core.answer_policy import reference_date
from apps.core.semantic_layer import parse_schema

CATEGORY_REVENUE = 'Rank product categories by total revenue over the last 6 months.'
MONTHLY_EXPENSES = 'Show monthly total recorded expenses over the last 12 months.'


def canonical_report_question(question: str) -> str | None:
    q = ' '.join(question.lower().split()).rstrip('.?!')
    period = r'(?:in|over|during|for) the (?:last|past|previous) (?:six|6) months'
    if re.fullmatch(
        r'(?:which product categories (?:generated|earned|had) the (?:most|highest) revenue|'
        r'show product categories by (?:total )?revenue) ' + period, q
    ):
        return CATEGORY_REVENUE
    year = r'(?:last|past|previous) (?:year|(?:12|twelve) months)'
    if any(re.fullmatch(pattern, q) for pattern in (
        r'for each month in the ' + year + r',? what (?:was|were) the total (?:recorded )?expenses?',
        r'what (?:was|were) the total (?:recorded )?expenses? for each month in the ' + year,
        r'show monthly total (?:recorded )?expenses? (?:for|over|in) the ' + year,
    )):
        return MONTHLY_EXPENSES
    # Accept our own canonical sentences when the router has already normalized.
    return next((intent for intent in (CATEGORY_REVENUE, MONTHLY_EXPENSES)
                 if q == intent.lower().rstrip('.')), None)


def report_assumptions(question: str) -> list[str]:
    intent = canonical_report_question(question)
    if intent is None:
        return []
    months = 6 if intent == CATEGORY_REVENUE else 12
    assumptions = [
        f'The period is a rolling {months}-calendar-month window through {reference_date()} '
        '(Asia/Kolkata), including that date and excluding future records.'
    ]
    if intent == CATEGORY_REVENUE:
        assumptions.append('Category revenue sums recorded sales line amounts, preferring net_amount, '
                           'then total_amount, then line_total; invoice totals are not repeated per item. '
                           'All recorded categories are ranked, with category as the tie-breaker.')
    else:
        assumptions.append('Expenses are summed by calendar month within that rolling window. '
                           'Only months with recorded expense rows are shown; missing months are not assumed to be zero.')
    return assumptions


def report_sql(schema: str, question: str, *, dialect: str = 'sqlite') -> str | None:
    intent = canonical_report_question(question)
    if intent is None:
        return None
    if dialect not in {'sqlite', 'postgres', 'postgresql', 'mysql'}:
        return None
    tables = parse_schema(schema)

    def unique(required):
        matches = [table for table in tables if required <= set(table.columns)]
        return matches[0] if len(matches) == 1 else None

    def quoted(identifier):
        quote = '`' if dialect == 'mysql' else '"'
        return quote + identifier.replace(quote, quote * 2) + quote

    today = reference_date()
    months = 12 if intent == MONTHLY_EXPENSES else 6
    year, month_index = divmod(today.year * 12 + today.month - 1 - months, 12)
    month = month_index + 1
    start = date(year, month, min(today.day, monthrange(year, month)[1])).isoformat()
    end = (today + timedelta(days=1)).isoformat()

    def day(column):
        return f'date({column})' if dialect == 'sqlite' else f'CAST({column} AS DATE)'

    if intent == MONTHLY_EXPENSES:
        expenses = unique({'expense_id', 'expense_date', 'amount'})
        if not expenses:
            return None
        month_expression = {
            'sqlite': "strftime('%Y-%m', e.expense_date)",
            'postgres': "TO_CHAR(CAST(e.expense_date AS TIMESTAMP), 'YYYY-MM')",
            'postgresql': "TO_CHAR(CAST(e.expense_date AS TIMESTAMP), 'YYYY-MM')",
            'mysql': "DATE_FORMAT(e.expense_date, '%Y-%m')",
        }[dialect]
        return f"""SELECT {month_expression} AS expense_month,
       SUM(e.amount) AS total_expenses
FROM {quoted(expenses.name)} AS e
WHERE {day('e.expense_date')} >= '{start}'
  AND {day('e.expense_date')} < '{end}'
GROUP BY {month_expression}
ORDER BY expense_month ASC"""
    products = unique({'product_id', 'category'})
    items = unique({'invoice_id', 'product_id'})
    invoices = unique({'invoice_id', 'invoice_date'})
    if not products or not items or not invoices:
        return None
    amount = next((column for column in ('net_amount', 'total_amount', 'line_total')
                   if column in items.columns), None)
    if amount is None:
        return None
    return f"""SELECT p.category, SUM(ii.{quoted(amount)}) AS total_revenue
FROM {quoted(items.name)} AS ii
JOIN {quoted(products.name)} AS p ON p.product_id = ii.product_id
JOIN {quoted(invoices.name)} AS si ON si.invoice_id = ii.invoice_id
WHERE {day('si.invoice_date')} >= '{start}'
  AND {day('si.invoice_date')} < '{end}'
GROUP BY p.category
ORDER BY total_revenue DESC, p.category ASC"""
