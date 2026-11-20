"""Privacy controls for what leaves the app and what lands in logs.

Puchoo.si sends the model the schema (table and column names, types) and the
user's question. Business data such as result rows, cell values and report
headers stays on the server unless an operator explicitly opts in with one of
the environment switches below. Every switch defaults to the private setting.
"""

from __future__ import annotations

import logging
import os
import re
import traceback
from pathlib import Path
from typing import Any

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

# Opt-in: send up to 50 executed result rows to the independent verifier model.
SHARE_RESULT_ROWS_ENV = "PUCHOO_LLM_SHARE_RESULT_ROWS"
# Opt-in: send result cell text (party names, item names) to the translation provider.
TRANSLATE_RESULT_VALUES_ENV = "PUCHOO_TRANSLATE_RESULT_VALUES"
# Opt-out: short categorical value hints (status/type/category/method columns) in the schema.
SCHEMA_VALUE_HINTS_ENV = "PUCHOO_SCHEMA_VALUE_HINTS"

# A workspace file with this sidecar is an imported financial report. Its
# sidecar carries report metadata (company, period) that never enters the
# queryable table or a model prompt, and value hints are always off for it.
REPORT_METADATA_SUFFIX = ".report.json"


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def share_result_rows_with_llm() -> bool:
    return _flag(SHARE_RESULT_ROWS_ENV, False)


def translate_result_values() -> bool:
    return _flag(TRANSLATE_RESULT_VALUES_ENV, False)


def schema_value_hints_enabled() -> bool:
    return _flag(SCHEMA_VALUE_HINTS_ENV, True)


def report_metadata_path(database_file: Path) -> Path:
    return database_file.with_name(database_file.name + REPORT_METADATA_SUFFIX)


def is_financial_report_file(database_file: Path | None) -> bool:
    return database_file is not None and report_metadata_path(database_file).is_file()


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Ten or more digits, optionally split by single spaces or hyphens: phone
# numbers, account numbers and large amounts. Dates and short counts survive.
_LONG_NUMBER = re.compile(r"(?<![\w.-])\+?(?:\d[ -]?){9,}\d(?![\w.-])")
_SINGLE_QUOTED = re.compile(r"'(?:''|[^'\n])*'")

_HINT_MAX_CHARS = 40


def safe_value_hint(value: object) -> bool:
    """A categorical hint is allowed only when it cannot be personal or financial data."""

    text = str(value).strip()
    if not text or len(text) > _HINT_MAX_CHARS:
        return False
    if _EMAIL.search(text) or any(character.isdigit() for character in text):
        return False
    return True


def scrub_text(text: str) -> str:
    """Remove values that commonly identify people or amounts from free text."""

    if not text:
        return text
    text = _EMAIL.sub("<email>", text)
    text = _LONG_NUMBER.sub("<number>", text)
    return _SINGLE_QUOTED.sub("'?'", text)


def safe_exception_text(exc_info: Any) -> str:
    """Traceback frames and the exception type only; exception messages can echo cell values."""

    try:
        exc_type, exc, tb = exc_info
    except (TypeError, ValueError):
        return ""
    if exc_type is None:
        return ""
    frames = "".join(traceback.format_tb(tb)) if tb is not None else ""
    return f"Traceback (most recent call last):\n{frames}{exc_type.__name__}: <message redacted>"


_INSTALLED_MARKER = "_puchoo_private_log_factory"


def install_log_redaction() -> None:
    """Scrub every log record at creation so no handler can print raw values.

    Messages are formatted once and scrubbed (emails, long numbers, quoted
    literals). Exception text keeps the stack but drops the message, which for
    pandas, SQLite and driver errors can contain the offending cell value.
    """

    current = logging.getLogRecordFactory()
    if getattr(current, _INSTALLED_MARKER, False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:  # A bad format string must not break logging.
            message = str(record.msg)
        record.msg = scrub_text(message)
        record.args = None
        if record.exc_info and record.exc_info[0] is not None:
            record.exc_text = safe_exception_text(record.exc_info)
        return record

    setattr(factory, _INSTALLED_MARKER, True)
    logging.setLogRecordFactory(factory)


__all__ = [
    "REPORT_METADATA_SUFFIX",
    "SCHEMA_VALUE_HINTS_ENV",
    "SHARE_RESULT_ROWS_ENV",
    "TRANSLATE_RESULT_VALUES_ENV",
    "install_log_redaction",
    "is_financial_report_file",
    "report_metadata_path",
    "safe_exception_text",
    "safe_value_hint",
    "schema_value_hints_enabled",
    "scrub_text",
    "share_result_rows_with_llm",
    "translate_result_values",
]
