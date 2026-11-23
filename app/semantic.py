"""Schema card for the LLM: column meanings (spec 01/02) + live types/values from DuckDB.

Types and allowed values are read from the warehouse at runtime so the prompt can't drift
from the data. Only the human meaning of each column is written by hand, here.
"""
from __future__ import annotations

import duckdb

COLUMN_DOCS: dict[str, dict[str, str]] = {
    "invoices": {
        "_table": "One row per invoice after dedup (5,000 rows). Grain: invoice_id.",
        "invoice_id": "unique invoice key",
        "account_id": "customer account key (join to accounts.account_id)",
        "account_name": "account name as written on THIS invoice (may differ across invoices)",
        "region": "region on this invoice",
        "plan": "plan on this invoice",
        "billing_cycle": "monthly or annual (annual invoices bill 10 months)",
        "seats": "licensed seats on the invoice",
        "currency": "invoice currency",
        "amount_local": "invoice amount in `currency`; negative for refunds",
        "amount_usd": "invoice amount converted to USD. USE THIS for any money sum/average.",
        "discount_pct": "discount percent applied (0-100)",
        "status": "canonical status. Only 'paid' is revenue; 'refunded' rows are negative and net against revenue",
        "invoice_date": "issue date (there is no payment date). Use for time bucketing",
        "invoice_year": "year of invoice_date",
        "invoice_month": "'YYYY-MM' of invoice_date",
        "mrr_usd": "monthly recurring revenue implied by this invoice (list price x seats x (1-discount)); not an amount billed",
        "counts_as_revenue": "TRUE for paid and refunded rows: SUM(amount_usd) WHERE counts_as_revenue = net recognized revenue",
        "is_future_dated": "invoice_date is after the pipeline run date",
        "churned": "churn flag as written on this invoice (prefer accounts.churned)",
        "csat_score": "CSAT on this invoice (prefer accounts.csat_score)",
    },
    "accounts": {
        "_table": "One row per customer account (3,815). Attributes are a SNAPSHOT from the account's latest invoice.",
        "account_id": "account key",
        "account_name": "name from latest invoice",
        "region": "region from latest invoice. Use for per-account/region questions",
        "plan": "current plan (latest invoice). Use for churn/MRR by plan",
        "churned": "has the account churned (latest invoice)",
        "is_active": "NOT churned",
        "csat_score": "latest CSAT 1-5, may be NULL",
        "current_mrr_usd": "account MRR in USD; 0 if churned. Use for any MRR question",
        "snapshot_mrr_usd": "MRR from latest invoice ignoring churn",
        "net_revenue_usd": "lifetime net recognized revenue (paid minus refunds) in USD. Use for 'top accounts by revenue'",
        "invoice_count": "number of invoices",
    },
}

HIDDEN_COLUMNS = {"status_raw", "expected_amount_usd", "amount_mismatch", "status_overridden",
                  "email_was_repaired", "email_invalid", "dedup_conflict_other_fields",
                  "fx_rate_to_usd", "list_price_usd", "months_billed", "snapshot_invoice_id",
                  "contact_email"}
ENUM_MAX_DISTINCT = 12


def schema_card(con: duckdb.DuckDBPyConnection) -> str:
    lines: list[str] = []
    for table, docs in COLUMN_DOCS.items():
        lines.append(f"TABLE {table} -- {docs['_table']}")
        for name, dtype in con.execute(f"SELECT column_name, data_type FROM information_schema.columns "
                                       f"WHERE table_name = '{table}' ORDER BY ordinal_position").fetchall():
            if name in HIDDEN_COLUMNS:
                continue
            desc = docs.get(name, "")
            values = ""
            if dtype == "VARCHAR" and not name.endswith("_id") and name not in ("account_name", "invoice_month"):
                distinct = [r[0] for r in con.execute(
                    f"SELECT DISTINCT {name} FROM {table} WHERE {name} IS NOT NULL ORDER BY 1 LIMIT {ENUM_MAX_DISTINCT + 1}").fetchall()]
                if len(distinct) <= ENUM_MAX_DISTINCT:
                    values = " | values: " + ", ".join(repr(v) for v in distinct)
            lines.append(f"  {name} {dtype}{' -- ' + desc if desc else ''}{values}")
        lines.append("")
    return "\n".join(lines)
