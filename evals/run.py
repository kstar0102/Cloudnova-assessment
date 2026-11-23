"""NL->SQL eval harness (spec 04, layer 2). `python -m evals.run [--only ID ...]`

Execution-based grading: the model's SQL result is compared with the golden SQL result.
Needs ANTHROPIC_API_KEY. Exit code 1 if any `expect: pass` case fails.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import yaml

from app.config import ROOT, connect_readonly, get_settings

CASES = ROOT / "evals" / "cases.yaml"
RESULTS = ROOT / "evals" / "results"
REL_TOL = 0.005


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _close(a, b) -> bool:
    return a is not None and b is not None and math.isclose(a, b, rel_tol=REL_TOL, abs_tol=0.01)


def grade(case: dict, ans, gold_cols: list[str], gold_rows: list[tuple]) -> tuple[bool, str]:
    mode = case["compare"]
    if mode == "refuse":
        ok = (not ans.answerable) or ans.guardrail_rejected
        return ok, "refused" if ok else f"did not refuse (sql: {ans.sql[:80]})"
    if ans.error or not ans.answerable:
        return False, ans.error or f"marked unanswerable: {ans.interpretation}"
    if not ans.rows:
        return False, "no rows returned"
    got = ans.rows
    if mode == "scalar":
        g, e = _num(got[0][0]), _num(gold_rows[0][0])
        return _close(g, e), f"got {g} expected {e}"
    if mode == "top_value":
        g, e = str(got[0][0]).lower(), str(gold_rows[0][0]).lower()
        return g == e, f"got {g} expected {e}"
    if mode == "set":
        key = case.get("key_column")
        idx = ans.columns.index(key) if key in ans.columns else 0
        gidx = gold_cols.index(key) if key in gold_cols else 0
        g, e = {str(r[idx]) for r in got}, {str(r[gidx]) for r in gold_rows}
        return g == e, f"got {sorted(g)} expected {sorted(e)}"
    if mode == "row":
        g = sorted(x for x in map(_num, got[0]) if x is not None)
        e = sorted(x for x in map(_num, gold_rows[0]) if x is not None)
        ok = len(g) >= len(e) and all(any(_close(gv, ev) for gv in g) for ev in e)
        return ok, f"got {g} expected {e}"
    if mode == "keyed":
        exp = {str(r[0]).lower(): _num(r[1]) for r in gold_rows}
        gmap = {}
        for r in got:
            nums = [x for x in map(_num, r[1:]) if x is not None]
            if nums:
                gmap[str(r[0]).lower()] = nums[0]
        ok = all(k in gmap and (_close(gmap[k], v) or _close(gmap[k], v * 100)) for k, v in exp.items())
        return ok, f"got {gmap} expected {exp}"
    raise ValueError(f"unknown compare mode {mode}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="case ids to run")
    args = ap.parse_args(argv)
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("ANTHROPIC_AUTH_TOKEN"):
        print("SKIPPED: ANTHROPIC_API_KEY not set. Layer-1 evals (no key) run via `pytest -q`.")
        return 0

    from app import pipeline
    from app.nl_query import NLQueryEngine

    s = get_settings()
    if not s.db_path.exists():
        pipeline.run(s)
    cases = yaml.safe_load(CASES.read_text())
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]

    gold_con = connect_readonly(s.db_path)
    eng = NLQueryEngine(s)
    results, unexpected = [], 0
    try:
        for case in cases:
            gold_cols, gold_rows = [], []
            if case.get("golden_sql"):
                cur = gold_con.execute(case["golden_sql"])
                gold_cols, gold_rows = [d[0] for d in cur.description], cur.fetchall()
            ans = eng.answer(case["question"])
            passed, detail = grade(case, ans, gold_cols, gold_rows)
            expected_pass = case.get("expect", "pass") == "pass"
            status = ("PASS" if passed else "FAIL") if expected_pass else ("XFAIL" if not passed else "XPASS")
            unexpected += status in ("FAIL", "XPASS") and expected_pass
            print(f"{status:5} {case['id']:32} {ans.latency_ms:6} ms  {ans.input_tokens:6}/{ans.output_tokens:<5} tok  {detail[:110]}")
            if status in ("FAIL", "XPASS"):
                print(f"      sql: {ans.sql}")
            if status == "XFAIL":
                print(f"      known failure: {case.get('reason', '')}")
            results.append({"id": case["id"], "status": status, "detail": detail, **ans.to_dict()})
    finally:
        eng.close()
        gold_con.close()

    n_pass = sum(r["status"] in ("PASS", "XFAIL") for r in results)
    lat = sorted(r["latency_ms"] for r in results)
    tok_in, tok_out = sum(r["input_tokens"] for r in results), sum(r["output_tokens"] for r in results)
    print(f"\n{n_pass}/{len(results)} as expected | model={s.model} effort={s.effort} fx_mode={s.fx_mode} | "
          f"median latency {lat[len(lat)//2] if lat else 0} ms | tokens in {tok_in} out {tok_out}")
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"model": s.model, "effort": s.effort, "fx_mode": s.fx_mode, "results": results}, indent=2, default=str))
    print(f"results -> {out.relative_to(ROOT)}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
