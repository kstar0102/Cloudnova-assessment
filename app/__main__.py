"""CLI. `python -m app build | ask "<question>" | demo`  (spec 03)."""
from __future__ import annotations

import argparse
import json
import os
import sys

from app import pipeline
from app.config import get_settings

DEMO_QUESTIONS = [
    "What was our total recognized revenue in USD for paid invoices in 2024?",
    "Which region has the highest average MRR per account?",
    "How much have we given back in refunds?",
    "What's the churn rate among Enterprise accounts vs Starter?",
    "List the top 5 accounts by revenue, and flag any with CSAT <= 2.",
    "How many invoices are stuck in pending or failed, and what's the exposed dollar amount?",
]


def _fmt(v) -> str:
    if isinstance(v, float) or type(v).__name__ == "Decimal":
        return f"{float(v):,.4f}" if abs(float(v)) < 1 else f"{float(v):,.2f}"
    return "NULL" if v is None else str(v)


def print_table(columns: list[str], rows: list[tuple], max_rows: int = 20) -> None:
    cells = [[_fmt(v) for v in r] for r in rows[:max_rows]]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in cells:
        print("  " + "  ".join(v.rjust(w) if v[:1].isdigit() or v[:1] == "-" else v.ljust(w) for v, w in zip(r, widths)))
    if len(rows) > max_rows:
        print(f"  ... {len(rows) - max_rows} more rows")


def print_answer(ans) -> None:
    print(f"\nQ: {ans.question}")
    if ans.interpretation:
        print(f"Interpretation: {ans.interpretation}")
    for a in ans.assumptions:
        print(f"  assumption: {a}")
    if ans.sql:
        print("SQL:\n  " + ans.sql.replace("\n", "\n  "))
    if ans.error:
        print(f"ERROR: {ans.error}")
    elif not ans.answerable:
        print("Not answerable from this data.")
    else:
        print("Result:")
        print_table(ans.columns, ans.rows)
    print(f"[{ans.latency_ms} ms, {ans.llm_calls} LLM call(s), {ans.input_tokens} in / {ans.output_tokens} out tokens]")


def ensure_built(s) -> None:
    if not s.db_path.exists():
        print(f"Warehouse not found at {s.db_path}; building it first...", file=sys.stderr)
        cmd_build(s)


def cmd_build(s) -> int:
    report = pipeline.run(s)
    print(f"Built {s.db_path}  (FX_MODE={s.fx_mode})")
    print(json.dumps(report, indent=2))
    return 0


def _engine(s):
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("ANTHROPIC_AUTH_TOKEN"):
        print("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add a key. "
              "(`python -m app build` and `pytest` work without one.)", file=sys.stderr)
        sys.exit(2)
    from app.nl_query import NLQueryEngine
    ensure_built(s)
    return NLQueryEngine(s)


def cmd_ask(s, question: str, as_json: bool) -> int:
    eng = _engine(s)
    try:
        ans = eng.answer(question)
    finally:
        eng.close()
    print(json.dumps(ans.to_dict(), indent=2)) if as_json else print_answer(ans)
    return 1 if ans.error else 0


def cmd_demo(s) -> int:
    cmd_build(s)
    eng = _engine(s)
    failures = 0
    try:
        for q in DEMO_QUESTIONS:
            ans = eng.answer(q)
            print_answer(ans)
            failures += bool(ans.error)
    finally:
        eng.close()
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app", description="CloudNova: ask the invoice ledger in plain English")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="clean the raw CSV into build/cloudnova.duckdb")
    a = sub.add_parser("ask", help="answer one question")
    a.add_argument("question")
    a.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("demo", help="build, then answer the stakeholder questions")
    args = p.parse_args(argv)
    s = get_settings()
    if args.cmd == "build":
        return cmd_build(s)
    if args.cmd == "ask":
        return cmd_ask(s, args.question, args.json)
    return cmd_demo(s)


if __name__ == "__main__":
    sys.exit(main())
