"""Spec 01 §T/§C rules, fed with the literal dirty variants from docs/data_profile.md."""
from datetime import date

import pytest

from app import parsers as P


@pytest.mark.parametrize("raw,expected", [
    ("4900.00", 4900.0), ("$4,900.00", 4900.0), ("31432", 31432.0), ("1,200.00 EUR", 1200.0),
    ("9,999.99 USD", 9999.99), ("€999.99", 999.99), ("£9,999.99", 9999.99),
    ("$-4,900.00", -4900.0), ("-1960.00", -1960.0), ("", None), (None, None), ("n/a", None),
    (float("nan"), None),
])
def test_amount(raw, expected):
    assert P.parse_amount(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2024-01-05", date(2024, 1, 5)),
    ("2024/01/05", date(2024, 1, 5)),
    ("01/05/2024", date(2024, 1, 5)),   # slash = month-first (F4)
    ("05-01-2024", date(2024, 1, 5)),   # dash = day-first (F3)
    ("19-07-2023", date(2023, 7, 19)),
    ("Jan 5 2024", date(2024, 1, 5)),
    ("02/30/2024", None),               # invalid calendar date -> NULL, not a guess
    ("yesterday", None), (None, None),
])
def test_date(raw, expected):
    assert P.parse_date(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Paid", "paid"), ("PAID", "paid"), ("Complete", "paid"), ("completed", "paid"), ("success", "paid"),
    ("in_review", "pending"), ("awaiting", "pending"), ("declined", "failed"), ("error", "failed"),
    ("REFUND", "refunded"), ("charged_back", "refunded"), ("cancelled", "void"), ("canceled", "void"),
    ("mystery", None),
])
def test_status(raw, expected):
    assert P.norm_category(raw, P.STATUS_MAP) == expected


@pytest.mark.parametrize("raw,expected", [
    (" Starter ", "starter"), ("Start", "starter"), ("Tier 1", "starter"), ("PRO", "pro"),
    ("Pro ", "pro"), ("Professional", "pro"), ("Tier 2", "pro"), ("ENT", "enterprise"),
    ("enterprise ", "enterprise"), ("Tier 3", "enterprise"),
])
def test_plan(raw, expected):
    assert P.norm_category(raw, P.PLAN_MAP) == expected


@pytest.mark.parametrize("raw,expected", [("annually", "annual"), ("yearly", "annual"), ("Annual", "annual"), ("Monthly", "monthly")])
def test_cycle(raw, expected):
    assert P.norm_category(raw, P.CYCLE_MAP) == expected


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), ("Yes", True), ("1", True), ("Y", True),
    ("false", False), ("FALSE", False), ("No", False), ("0", False), ("N", False), ("maybe", None),
])
def test_bool(raw, expected):
    assert P.parse_bool(raw) is expected


def test_email():
    assert P.parse_email("billing_at_globex.com") == ("billing@globex.com", True, False)
    assert P.parse_email("Billing@Initech.com") == ("billing@initech.com", False, False)
    assert P.parse_email("") == (None, False, False)
    assert P.parse_email("not an email") == (None, False, True)
