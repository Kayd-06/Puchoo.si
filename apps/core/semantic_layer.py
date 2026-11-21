"""Structured intent planning and schema focusing for text-to-SQL.

The planner is deliberately deterministic: models receive an explicit plan but
cannot redefine registered business metrics or silently broaden the schema.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class TableDefinition:
    name: str
    columns: tuple[str, ...]
    block: str


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    phrases: tuple[str, ...]
    required_columns: tuple[str, ...]
    formula: str
    grain: str
    output_alias: str


@dataclass
class QueryPlan:
    question: str
    tables: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    metric_definitions: dict[str, str] = field(default_factory=dict)
    filters: list[str] = field(default_factory=list)
    time_range: str | None = None
    order_by: str | None = None
    limit: int | None = None
    grain: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


METRIC_REGISTRY: tuple[MetricDefinition, ...] = (
    MetricDefinition("unpaid_invoice_count", ("unpaid invoice count",), ("invoice_id", "payment_status"), "Conditional count of distinct invoices where LOWER(invoice payment_status) = 'unpaid'.", "customer", "unpaid_invoice_count"),
    MetricDefinition("partial_invoice_count", ("partial invoice count",), ("invoice_id", "payment_status"), "Conditional count of distinct invoices where LOWER(invoice payment_status) = 'partial'.", "customer", "partial_invoice_count"),
    MetricDefinition("total_billed_amount", ("total billed", "billed amount"), ("invoice_id", "total_amount"), "SUM invoice total_amount once at invoice grain.", "customer", "total_billed_amount"),
    MetricDefinition("total_paid_amount", ("total paid", "payments received", "collected payments"), ("invoice_id", "amount_paid"), "Pre-aggregate SUM(amount_paid) by invoice_id, then sum those invoice payment totals.", "customer", "total_paid_amount"),
    MetricDefinition("outstanding_balance", ("outstanding balance", "remaining balance", "total due"), ("invoice_id", "total_amount", "amount_paid"), "SUM(invoice total_amount - COALESCE(pre-aggregated invoice payments, 0)).", "customer", "remaining_balance"),
    MetricDefinition("invoice_count", ("invoice count", "number of invoices"), ("invoice_id",), "COUNT(DISTINCT invoice_id).", "group", "number_of_invoices"),
    MetricDefinition("sales_value", ("sales value", "net sales", "gross sales"), ("line_total",), "SUM(invoice_items.line_total).", "group", "total_sales_value"),
    MetricDefinition("quantity_sold", ("quantity sold", "best-selling", "most sold"), ("quantity", "invoice_id", "product_id"), "SUM(invoice_items.quantity), never inventory movement quantity.", "product", "total_quantity_sold"),
    MetricDefinition("average_discount", ("average discount",), ("discount_amount",), "AVG(invoice_items.discount_amount).", "group", "average_discount"),
    MetricDefinition("expense_total", ("total expenses", "amount spent", "spending"), ("expense_id", "amount"), "SUM(expenses.amount).", "department", "total_expenses"),
    MetricDefinition("budget", ("monthly budget", "budget"), ("monthly_budget",), "Use departments.monthly_budget without summing duplicated join rows.", "department", "monthly_budget"),
    MetricDefinition("attendance_average", ("overall attendance", "average attendance", "mean attendance"), ("attendance_percentage",), "AVG(attendance_percentage), filtered with HAVING when a threshold is requested.", "student", "overall_attendance_percentage"),
    MetricDefinition("revenue", ("revenue", "invoice value"), ("total_amount",), "SUM(invoice total_amount) unless the question explicitly asks for line-item sales value.", "group", "total_revenue"),
)


DIMENSION_HINTS = {
    "customer": ("customer_id", "customer_name"),
    "product": ("product_id", "product_name"),
    "category": ("category",),
    "department": ("department_id", "department_name"),
    "student": ("student_id", "full_name"),
    "vendor": ("vendor_id", "vendor_name"),
    "employee": ("employee_id", "full_name"),
}


def parse_schema(schema: str) -> list[TableDefinition]:
    tables: list[TableDefinition] = []
    for block in re.split(r"\n\s*\n", schema.strip()):
        match = re.match(r"Table:\s*([^\n]+)\nColumns:\s*([^\n]+)", block)
        if not match:
            continue
        columns = tuple(part.strip().split(" ", 1)[0] for part in match.group(2).split(","))
        tables.append(TableDefinition(match.group(1).strip(), columns, block.strip()))
    return tables


def _contains_phrase(question: str, phrases: Iterable[str]) -> bool:
    return any(phrase in question for phrase in phrases)


def build_query_plan(schema: str, question: str) -> QueryPlan:
    q = question.lower().strip()
    tables = parse_schema(schema)
    plan = QueryPlan(question=question)

    definitions = [metric for metric in METRIC_REGISTRY if _contains_phrase(q, metric.phrases)]
    # Prefer specific metrics over their generic substrings.
    if any(metric.name == "outstanding_balance" for metric in definitions):
        definitions = [metric for metric in definitions if metric.name != "revenue"]
    if any(metric.name in {"unpaid_invoice_count", "partial_invoice_count"} for metric in definitions):
        if "number of invoices" not in q and "total invoice count" not in q:
            definitions = [metric for metric in definitions if metric.name != "invoice_count"]
    plan.metrics = [metric.name for metric in definitions]
    plan.metric_definitions = {metric.name: metric.formula for metric in definitions}

    for dimension, columns in DIMENSION_HINTS.items():
        if re.search(rf"\b{re.escape(dimension)}s?\b", q):
            plan.dimensions.append(dimension)
            for table in tables:
                if set(columns) <= set(table.columns):
                    plan.tables.append(table.name)

    required_columns = {column for metric in definitions for column in metric.required_columns}
    for column in required_columns:
        candidates = [table for table in tables if column in table.columns]
        # Prefer canonical fact tables over broad tables that merely share a key.
        candidates.sort(key=lambda table: (len(required_columns & set(table.columns)), len(table.columns)), reverse=True)
        if candidates:
            plan.tables.append(candidates[0].name)

    question_tokens = set(re.findall(r"[a-z][a-z0-9_]+", q))
    for table in tables:
        identifier_tokens = set(table.name.lower().split("_")) | {column.lower() for column in table.columns}
        if len(question_tokens & identifier_tokens) >= 2:
            plan.tables.append(table.name)

    plan.tables = list(dict.fromkeys(plan.tables))

    if re.search(r"\b(unpaid|partial|outstanding|pending)\b", q):
        plan.filters.append("Use invoice payment_status values case-insensitively; outstanding invoices are unpaid or partial.")
    threshold = re.search(r"(?:greater than|above|over)\s+(\d+(?:\.\d+)?)\s*(?:percent|%)?", q)
    if threshold:
        plan.filters.append(f"Requested lower threshold: {threshold.group(1)}.")

    rolling = re.search(r"rolling\s+(\d+)\s+(day|week|month|year)s?", q)
    if rolling:
        plan.time_range = f"rolling {rolling.group(1)} {rolling.group(2)}s ending today"
    elif "last month" in q:
        plan.time_range = "previous complete calendar month"
    elif "this month" in q:
        plan.time_range = "current calendar month"

    top = re.search(r"\btop\s+(\d+)\b", q)
    if top:
        plan.limit = min(int(top.group(1)), 500)
    if re.search(r"\b(top|highest|best|rank)\b", q) and plan.metrics:
        ranked_metric = next(
            (
                metric.name
                for metric in definitions
                if re.search(rf"(?:rank(?:ed)?|top|highest|best)[\s\S]*\bby\s+{re.escape(metric.phrases[0])}\b", q)
            ),
            plan.metrics[-1],
        )
        plan.order_by = f"{ranked_metric} descending"
    grains = [metric.grain for metric in definitions if metric.grain not in {"group"}]
    plan.grain = plan.dimensions[-1] if plan.dimensions else (grains[0] if grains else None)
    return plan


def select_relevant_schema(schema: str, plan: QueryPlan) -> str:
    tables = parse_schema(schema)
    selected = set(plan.tables)
    if not selected:
        return schema

    blocks = [table.block for table in tables if table.name in selected]
    selected_defs = [table for table in tables if table.name in selected]
    key_tables: dict[str, list[str]] = {}
    for table in selected_defs:
        for column in table.columns:
            if column.endswith("_id"):
                key_tables.setdefault(column, []).append(table.name)
    hints = [
        f"{column}: " + ", ".join(f"{table}.{column}" for table in names)
        for column, names in key_tables.items() if len(names) > 1
    ]
    if hints:
        blocks.append("Join candidates (same key name; use only when relevant):\n" + "\n".join(hints))
    return "\n\n".join(blocks) if blocks else schema


_FOCUS_STOPWORDS = {
    "the", "and", "for", "with", "from", "what", "which", "show", "list", "give", "total", "all",
    "how", "many", "much", "per", "each", "this", "that", "was", "were", "are", "is", "of", "in",
    "on", "by", "to", "me", "my", "our", "a", "an", "vs", "versus", "compare", "between", "data",
}


def _focus_int(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(lower, min(value, upper))


def _stem(word: str) -> str:
    word = word.lower()
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _identifier_words(value: str) -> set[str]:
    return {_stem(part) for part in re.split(r"[^a-z0-9]+", value.lower()) if len(part) > 1}


def focus_schema_for_question(
    schema: str, question: str, *, focus_after: int | None = None, max_tables: int | None = None
) -> tuple[str, list[str]]:
    """Keep only the tables a question is about when a source has many tables.

    Deterministic and local: question words are matched against table names
    (strong signal) and column names that are not shared by most tables (weak
    signal). A source with few tables, or a question that matches nothing,
    keeps the whole schema. Returns the schema text and the chosen table
    names (empty when nothing was dropped).
    """

    tables = parse_schema(schema)
    focus_after = _focus_int("PUCHOO_SCHEMA_FOCUS_AFTER_TABLES", 5, 1, 200) if focus_after is None else focus_after
    max_tables = _focus_int("PUCHOO_SCHEMA_FOCUS_MAX_TABLES", 6, 1, 50) if max_tables is None else max_tables
    if len(tables) <= focus_after:
        return schema, []
    words = {_stem(word) for word in re.findall(r"[a-z][a-z0-9]+", question.lower())} - _FOCUS_STOPWORDS
    if not words:
        return schema, []
    column_words = [set().union(*(_identifier_words(column) for column in table.columns)) for table in tables]
    shared = {
        word for word in set().union(*column_words)
        if sum(word in item for item in column_words) * 2 > len(tables)
    }
    scored: list[tuple[int, int, TableDefinition]] = []
    for position, (table, columns) in enumerate(zip(tables, column_words)):
        score = 3 * len(words & _identifier_words(table.name)) + len(words & (columns - shared))
        if score:
            scored.append((score, -position, table))
    if not scored:
        return schema, []
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    chosen = {table.name for _, _, table in scored[:max_tables]}
    if len(chosen) >= len(tables):
        return schema, []
    selected = [table for table in tables if table.name in chosen]
    blocks = [table.block for table in selected]
    key_tables: dict[str, list[str]] = {}
    for table in selected:
        for column in table.columns:
            if column.endswith("_id"):
                key_tables.setdefault(column, []).append(table.name)
    hints = [
        f"{column}: " + ", ".join(f"{name}.{column}" for name in names)
        for column, names in key_tables.items() if len(names) > 1
    ]
    if hints:
        blocks.append("Join candidates (same key name; use only when relevant):\n" + "\n".join(hints))
    return "\n\n".join(blocks), [table.name for table in selected]


def render_planned_question(plan: QueryPlan) -> str:
    payload = json.dumps(plan.as_dict(), ensure_ascii=False, separators=(",", ":"))
    return (
        f"{plan.question}\n\n"
        "Validated intent plan (authoritative; do not add metrics, filters, tables, or time ranges):\n"
        f"{payload}"
    )


def plan_semantic_feedback(plan: QueryPlan, sql: str) -> list[str]:
    sql_lower = sql.lower()
    feedback: list[str] = []
    referenced = set(re.findall(r"(?:from|join)\s+[\"`\[]?([a-z_][\w]*)", sql_lower))
    allowed = {table.lower() for table in plan.tables}
    unexpected = sorted(referenced - allowed) if allowed else []
    # CTE names are legal even though they are not schema tables.
    ctes = set(re.findall(r"(?:with|,)\s*([a-z_][\w]*)\s+as\s*\(", sql_lower))
    unexpected = [table for table in unexpected if table not in ctes]
    if unexpected:
        feedback.append(f"Remove tables outside the validated intent plan: {', '.join(unexpected)}.")

    if len(plan.metrics) >= 2:
        missing_aliases = []
        by_name = {metric.name: metric for metric in METRIC_REGISTRY}
        for metric_name in plan.metrics:
            alias = by_name[metric_name].output_alias
            if alias not in sql_lower:
                missing_aliases.append(alias)
        if missing_aliases:
            feedback.append("Return every requested metric with these output aliases: " + ", ".join(missing_aliases) + ".")
    return feedback


__all__ = [
    "METRIC_REGISTRY",
    "MetricDefinition",
    "QueryPlan",
    "TableDefinition",
    "build_query_plan",
    "focus_schema_for_question",
    "parse_schema",
    "plan_semantic_feedback",
    "render_planned_question",
    "select_relevant_schema",
]
