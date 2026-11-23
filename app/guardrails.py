"""SQL guardrails (spec 03 step 3). Pure function: SQL in -> safe SQL out, or GuardrailError."""
from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = frozenset({"invoices", "accounts"})
FORBIDDEN_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
                   exp.Command, exp.Merge, exp.Copy, exp.Pragma, exp.Set, exp.Use, exp.Attach,
                   exp.Detach, exp.Install)
# File / network / environment readers DuckDB exposes as functions.
FORBIDDEN_FUNCTIONS = frozenset({
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
    "read_blob", "parquet_scan", "glob", "getenv", "query", "query_table", "sniff_csv",
})


class GuardrailError(ValueError):
    pass


def check_and_limit(sql: str, row_limit: int = 200) -> str:
    if not sql or not sql.strip():
        raise GuardrailError("empty SQL")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise GuardrailError(f"SQL does not parse: {e}") from e
    if len(statements) != 1:
        raise GuardrailError(f"exactly one statement allowed, got {len(statements)}")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise GuardrailError(f"only SELECT queries are allowed, got {tree.key.upper()}")
    bad = tree.find(*FORBIDDEN_NODES)
    if bad is not None:
        raise GuardrailError(f"forbidden operation: {bad.key.upper()}")

    for fn in tree.find_all(exp.Func):
        name = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
        if name in FORBIDDEN_FUNCTIONS:
            raise GuardrailError(f"forbidden function: {name}")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise GuardrailError(f"table functions are not allowed: {table.sql(dialect='duckdb')}")
        if table.args.get("db") or table.args.get("catalog"):
            raise GuardrailError(f"qualified table names are not allowed: {table.sql(dialect='duckdb')}")
        name = table.name.lower()
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise GuardrailError(f"unknown table: {name} (allowed: {', '.join(sorted(ALLOWED_TABLES))})")

    if tree.args.get("limit") is None:
        tree = tree.limit(row_limit) if isinstance(tree, exp.Select) else exp.select("*").from_(tree.subquery("q")).limit(row_limit)
    return tree.sql(dialect="duckdb")
