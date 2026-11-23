"""Pure, single-value parsing rules from spec 01 §T and §C.

Each function returns None for values it cannot interpret (rule 4: unknown is not zero).
"""
from __future__ import annotations

import re
from datetime import date

PLAN_MAP = {
    "starter": "starter", "start": "starter", "tier 1": "starter",
    "pro": "pro", "professional": "pro", "tier 2": "pro",
    "enterprise": "enterprise", "ent": "enterprise", "tier 3": "enterprise",
}
CYCLE_MAP = {"monthly": "monthly", "annual": "annual", "annually": "annual", "yearly": "annual"}
STATUS_MAP = {
    "paid": "paid", "complete": "paid", "completed": "paid", "success": "paid",
    "pending": "pending", "in_review": "pending", "awaiting": "pending",
    "failed": "failed", "declined": "failed", "error": "failed",
    "refunded": "refunded", "refund": "refunded", "charged_back": "refunded",
    "void": "void", "cancelled": "void", "canceled": "void",
}
PAYMENT_MAP = {
    "credit_card": "credit_card", "credit card": "credit_card",
    "ach": "ach", "wire": "wire", "wire transfer": "wire", "invoice": "invoice",
}
BOOL_TRUE = {"true", "yes", "y", "1"}
BOOL_FALSE = {"false", "no", "n", "0"}

# Spec 01 §D2: higher wins when the same invoice_id has conflicting statuses.
STATUS_PRECEDENCE = {"refunded": 5, "paid": 4, "failed": 3, "void": 2, "pending": 1}


def _str(value: object) -> str | None:
    """Missing-aware coercion: None/NaN/blank -> None, else stripped str."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _key(value: object) -> str | None:
    s = _str(value)
    return re.sub(r"\s+", " ", s.lower()) if s else None


def norm_category(value: str | None, mapping: dict[str, str]) -> str | None:
    k = _key(value)
    return mapping.get(k) if k else None


def parse_bool(value: str | None) -> bool | None:
    k = _key(value)
    if k in BOOL_TRUE:
        return True
    if k in BOOL_FALSE:
        return False
    return None


_AMOUNT_STRIP = re.compile(r"[$€£,\s]|USD|EUR|GBP", re.IGNORECASE)


def parse_amount(value: str | None) -> float | None:
    """'$4,900.00' -> 4900.0; '$-4,900.00' -> -4900.0; '1,200.00 EUR' -> 1200.0; '31432' -> 31432.0"""
    value = _str(value)
    if value is None:
        return None
    s = _AMOUNT_STRIP.sub("", value)
    negative = "-" in s
    s = s.replace("-", "")
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    amount = round(float(s), 2)
    return -amount if negative else amount


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_date(value: str | None) -> date | None:
    """Per-format convention (spec 01 §T, data_profile F3/F4):
    YYYY-MM-DD, YYYY/MM/DD, MM/DD/YYYY (slash = month-first), DD-MM-YYYY (dash = day-first), 'Mon D YYYY'.
    """
    s = _str(value)
    if s is None:
        return None
    try:
        if m := re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s):
            y, mo, d = map(int, m.groups())
        elif m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s):
            mo, d, y = map(int, m.groups())
        elif m := re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{4})", s):
            d, mo, y = map(int, m.groups())
        elif m := re.fullmatch(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})", s):
            mo = _MONTHS.get(m.group(1).lower())
            if mo is None:
                return None
            d, y = int(m.group(2)), int(m.group(3))
        else:
            return None
        return date(y, mo, d)
    except ValueError:  # e.g. 31/02
        return None


def parse_email(value: str | None) -> tuple[str | None, bool, bool]:
    """Returns (email, was_repaired, is_invalid)."""
    s = _str(value)
    if s is None:
        return None, False, False
    s = s.lower()
    repaired = False
    if "@" not in s and "_at_" in s:
        s = s.replace("_at_", "@", 1)
        repaired = True
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", s):
        return None, repaired, True
    return s, repaired, False


def parse_int(value: str | None, lo: int | None = None, hi: int | None = None) -> int | None:
    value = _str(value)
    if value is None:
        return None
    try:
        f = float(value)
    except ValueError:
        return None
    if not f.is_integer():
        return None
    i = int(f)
    if (lo is not None and i < lo) or (hi is not None and i > hi):
        return None
    return i


def parse_float(value: str | None) -> float | None:
    value = _str(value)
    if value is None:
        return None
    try:
        return float(value.rstrip("%"))
    except ValueError:
        return None
