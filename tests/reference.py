"""Independent reference implementation of the stakeholder metrics.

Deliberately shares NO code with app/: stdlib csv, its own parsing, row-by-row Python.
If the pandas+DuckDB pipeline and this file agree, the number is very likely right
(two implementations of the same spec); if they disagree, one has a bug.
Spec references: 01 §T/§C/§D/§A, 02.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import date

PRICE = {"starter": 49, "pro": 99, "enterprise": 299}
FX = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}
STATUS = {}
for canon, variants in {
    "paid": "paid complete completed success",
    "pending": "pending in_review awaiting",
    "failed": "failed declined error",
    "refunded": "refunded refund charged_back",
    "void": "void cancelled canceled",
}.items():
    for v in variants.split():
        STATUS[v] = canon
RANK = ["pending", "void", "failed", "paid", "refunded"]  # higher index wins
PLAN = {"starter": "starter", "start": "starter", "tier 1": "starter", "pro": "pro",
        "professional": "pro", "tier 2": "pro", "enterprise": "enterprise", "ent": "enterprise",
        "tier 3": "enterprise"}
MON = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def money(s: str) -> float:
    neg = "-" in s
    digits = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    return -float(digits) if neg else float(digits)


def day(s: str) -> date:
    s = s.strip()
    if re.match(r"^\d{4}", s):
        y, m, d = re.split(r"[-/]", s)
    elif "/" in s:
        m, d, y = s.split("/")
    elif re.match(r"^\d", s):
        d, m, y = s.split("-")
    else:
        mon, d, y = s.replace(",", "").split()
        m = MON.index(mon[:3].lower()) + 1
    return date(int(y), int(m), int(d))


def load(path: str, fx_mode: str = "multiply") -> list[dict]:
    best: dict[str, dict] = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            amt = money(r["amount"])
            st = "refunded" if amt < 0 else STATUS[r["status"].strip().lower()]
            rate = FX[r["currency"].strip().upper()]
            row = {
                "id": r["invoice_id"].strip(), "acct": r["account_id"].strip(),
                "region": r["region"].strip().upper(),
                "plan": PLAN[" ".join(r["plan"].lower().split())],
                "seats": int(r["seats"]), "disc": float(r["discount_pct"]),
                "status": st, "usd": round(amt * rate if fx_mode == "multiply" else amt / rate, 2),
                "date": day(r["invoice_date"]),
                "churned": r["churned"].strip().lower() in ("true", "yes", "y", "1"),
                "csat": int(r["csat_score"]) if r["csat_score"].strip() else None,
            }
            cur = best.get(row["id"])
            if cur is None or RANK.index(st) > RANK.index(cur["status"]):
                best[row["id"]] = row
    return list(best.values())


def accounts(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    rev: dict[str, float] = defaultdict(float)
    for r in rows:
        if r["status"] in ("paid", "refunded"):
            rev[r["acct"]] += r["usd"]
        cur = out.get(r["acct"])
        if cur is None or (r["date"], r["id"]) > (cur["date"], cur["id"]):
            out[r["acct"]] = r
    result = {}
    for a, r in out.items():
        mrr = PRICE[r["plan"]] * r["seats"] * (1 - r["disc"] / 100)
        result[a] = {**r, "mrr": 0.0 if r["churned"] else mrr, "active": not r["churned"],
                     "revenue": round(rev[a], 2)}
    return result


def q1_paid_2024(rows):
    return round(sum(r["usd"] for r in rows if r["status"] == "paid" and r["date"].year == 2024), 2)


def q2_region_avg_mrr(accts):
    by = defaultdict(list)
    for a in accts.values():
        if a["active"]:
            by[a["region"]].append(a["mrr"])
    return {k: sum(v) / len(v) for k, v in by.items()}


def q3_refunds(rows):
    return round(-sum(r["usd"] for r in rows if r["status"] == "refunded"), 2)


def q4_churn(accts):
    by = defaultdict(list)
    for a in accts.values():
        by[a["plan"]].append(a["churned"])
    return {k: sum(v) / len(v) for k, v in by.items()}


def q5_top5(accts):
    return [a for a, _ in sorted(((k, v["revenue"]) for k, v in accts.items()), key=lambda t: -t[1])[:5]]


def q6_stuck(rows):
    stuck = [r for r in rows if r["status"] in ("pending", "failed")]
    return len(stuck), round(sum(r["usd"] for r in stuck), 2)
