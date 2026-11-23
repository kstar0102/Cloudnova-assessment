"""Spec 03 step 3: only single read-only SELECTs over invoices/accounts get through."""
import pytest

from app.guardrails import GuardrailError, check_and_limit


@pytest.mark.parametrize("sql", [
    "DELETE FROM invoices WHERE status = 'void'",
    "DROP TABLE invoices",
    "UPDATE invoices SET amount_usd = 0",
    "INSERT INTO invoices SELECT * FROM invoices",
    "CREATE TABLE x AS SELECT 1",
    "SELECT 1; DROP TABLE invoices",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_csv_auto('data/cloudnova_invoices.csv')",
    "SELECT getenv('ANTHROPIC_API_KEY')",
    "SELECT * FROM invoices_rejected",
    "SELECT * FROM information_schema.tables",
    "ATTACH 'other.db' AS o",
    "COPY invoices TO 'out.csv'",
    "PRAGMA database_list",
    "INSTALL httpfs",
    "",
    "SELEC amount FROM invoices",
])
def test_rejects(sql):
    with pytest.raises(GuardrailError):
        check_and_limit(sql)


@pytest.mark.parametrize("sql", [
    "SELECT SUM(amount_usd) FROM invoices WHERE status = 'paid'",
    "WITH t AS (SELECT account_id, SUM(amount_usd) s FROM invoices GROUP BY 1) SELECT * FROM t JOIN accounts USING (account_id)",
    "SELECT plan, AVG(CASE WHEN churned THEN 1.0 ELSE 0.0 END) FROM accounts GROUP BY plan",
    "SELECT region FROM accounts UNION SELECT region FROM invoices",
])
def test_allows(sql):
    assert check_and_limit(sql)


def test_adds_limit_only_when_missing():
    assert "LIMIT 200" in check_and_limit("SELECT * FROM invoices")
    assert "LIMIT 5" in check_and_limit("SELECT * FROM accounts LIMIT 5")
    assert "LIMIT 200" in check_and_limit("SELECT region FROM accounts UNION SELECT region FROM invoices")


def test_every_golden_sql_passes_guardrails(cases):
    for c in cases.values():
        if c.get("golden_sql"):
            check_and_limit(c["golden_sql"])
