"""Raw CSV -> cleaned `invoices`, `invoices_rejected`, `accounts` in DuckDB (spec 01)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import duckdb
import pandas as pd

from app import parsers as P
from app.config import FX_TO_USD, LIST_PRICE_USD, MONTHS_BILLED, Settings


class ValidationError(RuntimeError):
    pass


def load_raw(path: Path) -> pd.DataFrame:
    # Spec 01 step 1: everything as string; "NA" is a region, not a null.
    df = pd.read_csv(path, dtype=str, keep_default_na=False).astype(object)
    df = df.map(lambda v: v.strip() if isinstance(v, str) else v)
    return df.where(df.ne(""), None)


def normalize(raw: pd.DataFrame, dq: Counter) -> pd.DataFrame:
    out = pd.DataFrame(index=raw.index)
    out["invoice_id"] = raw["invoice_id"]
    out["account_id"] = raw["account_id"]
    out["account_name"] = raw["account_name"]

    emails = raw["contact_email"].map(P.parse_email)
    out["contact_email"] = emails.str[0]
    out["email_was_repaired"] = emails.str[1].astype(bool)
    out["email_invalid"] = emails.str[2].astype(bool)

    out["region"] = raw["region"].str.upper()
    out["industry"] = raw["industry"].str.title()
    out["plan"] = raw["plan"].map(lambda v: P.norm_category(v, P.PLAN_MAP))
    out["billing_cycle"] = raw["billing_cycle"].map(lambda v: P.norm_category(v, P.CYCLE_MAP))
    out["seats"] = raw["seats"].map(lambda v: P.parse_int(v, lo=1))
    out["currency"] = raw["currency"].str.upper()
    out["amount_local"] = raw["amount"].map(P.parse_amount)
    out["discount_pct"] = raw["discount_pct"].map(P.parse_float)
    out["status_raw"] = raw["status"]
    out["status"] = raw["status"].map(lambda v: P.norm_category(v, P.STATUS_MAP))
    out["payment_method"] = raw["payment_method"].map(lambda v: P.norm_category(v, P.PAYMENT_MAP))
    out["signup_date"] = raw["signup_date"].map(P.parse_date)
    out["invoice_date"] = raw["invoice_date"].map(P.parse_date)
    out["churned"] = raw["churned"].map(P.parse_bool)
    out["csat_score"] = raw["csat_score"].map(lambda v: P.parse_int(v, lo=1, hi=5))
    out["support_tickets"] = raw["support_tickets"].map(lambda v: P.parse_int(v, lo=0))

    # Sign override (spec 01 §C, profile F8): money moved backwards => refunded.
    neg = out["amount_local"].lt(0) & out["status"].ne("refunded")
    out.loc[neg, "status"] = "refunded"
    out["status_overridden"] = neg
    dq["status_overridden_negative_amount"] = int(neg.sum())

    # DQ counters: unparsed values that were present in raw.
    for col, rawcol in [("plan", "plan"), ("billing_cycle", "billing_cycle"), ("status", "status"),
                        ("payment_method", "payment_method"), ("amount_local", "amount"),
                        ("invoice_date", "invoice_date"), ("signup_date", "signup_date"),
                        ("churned", "churned"), ("csat_score", "csat_score"), ("seats", "seats")]:
        dq[f"unparsed_{col}"] = int((out[col].isna() & raw[rawcol].notna()).sum())
    for col in ["contact_email", "industry", "csat_score", "payment_method"]:
        dq[f"null_{col}"] = int(out[col].isna().sum())
    dq["email_repaired"] = int(out["email_was_repaired"].sum())
    dq["email_invalid"] = int(out["email_invalid"].sum())
    dq["unknown_region"] = int((~out["region"].isin(["NA", "EMEA", "APAC", "LATAM"])).sum())
    dq["unknown_currency"] = int((~out["currency"].isin(FX_TO_USD)).sum())
    return out


def dedup(df: pd.DataFrame, dq: Counter) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Spec 01 §D. Returns (kept, rejected-with-reason)."""
    compare_cols = [c for c in df.columns if c not in ("status_raw",)]
    # D1: exact duplicates after normalization. Compare on a string view so None/NaN/date compare cleanly.
    key = df[compare_cols].astype(str)
    exact = key.duplicated(keep="first")
    rej_exact = df[exact].assign(reject_reason="exact_duplicate")
    df = df[~exact]
    dq["dedup_exact_removed"] = int(exact.sum())

    # D2: conflicting rows for the same invoice_id -> status precedence.
    df = df.assign(_prec=df["status"].map(P.STATUS_PRECEDENCE).fillna(0))
    df = df.sort_values(["invoice_id", "_prec"], ascending=[True, False], kind="stable")
    loser = df.duplicated("invoice_id", keep="first")
    other_cols = [c for c in compare_cols if c not in ("status", "status_overridden", "payment_method")]
    dup_ids = df.loc[df["invoice_id"].duplicated(keep=False), "invoice_id"]
    dup_rows = df[df["invoice_id"].isin(dup_ids)]
    differs = dup_rows[other_cols].astype(str).groupby(dup_rows["invoice_id"]).nunique().gt(1).any(axis=1)
    df["dedup_conflict_other_fields"] = df["invoice_id"].map(differs).fillna(False).astype(bool)
    rej_conflict = df[loser].assign(reject_reason="conflicting_duplicate_lower_status_precedence")
    kept = df[~loser].drop(columns="_prec")
    dq["dedup_conflict_removed"] = int(loser.sum())
    dq["dedup_conflict_other_fields"] = int(kept["dedup_conflict_other_fields"].sum())

    rejected = pd.concat([rej_exact, rej_conflict.drop(columns="_prec")], ignore_index=True)
    return kept.reset_index(drop=True), rejected


def derive(df: pd.DataFrame, s: Settings, dq: Counter) -> pd.DataFrame:
    df = df.copy()
    rate = df["currency"].map(FX_TO_USD)
    df["fx_rate_to_usd"] = rate
    usd_mult = (df["amount_local"] * rate).round(2)
    usd_div = (df["amount_local"] / rate).round(2)
    df["amount_usd"] = usd_mult if s.fx_mode == "multiply" else usd_div
    df["list_price_usd"] = df["plan"].map(LIST_PRICE_USD)
    df["months_billed"] = df["billing_cycle"].map(MONTHS_BILLED)
    disc = 1 - df["discount_pct"].fillna(0) / 100
    df["mrr_usd"] = (df["list_price_usd"] * df["seats"] * disc).round(2)
    sign = df["amount_local"].apply(lambda a: -1 if a is not None and a < 0 else 1)
    df["expected_amount_usd"] = (df["list_price_usd"] * df["seats"] * df["months_billed"] * disc * sign).round(2)
    df["amount_mismatch"] = (usd_div - df["expected_amount_usd"]).abs() > 1.0
    df["counts_as_revenue"] = df["status"].isin(["paid", "refunded"])
    inv = pd.to_datetime(df["invoice_date"])
    df["invoice_year"] = inv.dt.year.astype("Int64")
    df["invoice_month"] = inv.dt.to_period("M").astype(str).where(inv.notna(), None)
    df["is_future_dated"] = inv.dt.date.map(lambda d: d is not None and not pd.isna(d) and d > s.run_date)
    dq["future_dated"] = int(df["is_future_dated"].sum())
    dq["amount_mismatch_vs_price_list"] = int(df["amount_mismatch"].sum())
    return df


def build_accounts(inv: pd.DataFrame) -> pd.DataFrame:
    """Spec 01 §A: snapshot = latest invoice per account (NULL dates last, tie -> highest invoice_id)."""
    ordered = inv.assign(_d=pd.to_datetime(inv["invoice_date"])).sort_values(
        ["account_id", "_d", "invoice_id"], ascending=[True, False, False], na_position="last")
    snap_cols = ["account_id", "account_name", "contact_email", "region", "industry", "plan",
                 "billing_cycle", "seats", "discount_pct", "churned", "csat_score", "mrr_usd", "invoice_id"]
    snap = ordered.drop_duplicates("account_id", keep="first")[snap_cols].rename(
        columns={"invoice_id": "snapshot_invoice_id", "mrr_usd": "snapshot_mrr_usd"})
    snap["is_active"] = snap["churned"].eq(False)
    snap["current_mrr_usd"] = snap["snapshot_mrr_usd"].where(snap["is_active"], 0.0)

    agg = inv.groupby("account_id").agg(
        invoice_count=("invoice_id", "count"),
        first_invoice_date=("invoice_date", "min"),
        last_invoice_date=("invoice_date", "max"),
        total_support_tickets=("support_tickets", "sum"),
    )
    rev = inv[inv["counts_as_revenue"]].groupby("account_id")["amount_usd"].sum().rename("net_revenue_usd")
    acc = snap.set_index("account_id").join(agg).join(rev)
    acc["net_revenue_usd"] = acc["net_revenue_usd"].fillna(0.0).round(2)
    return acc.reset_index()


def validate(raw: pd.DataFrame, inv: pd.DataFrame, rejected: pd.DataFrame) -> None:
    errors = []
    if len(raw) != len(inv) + len(rejected):
        errors.append(f"row accounting: raw={len(raw)} kept={len(inv)} rejected={len(rejected)}")
    if inv["invoice_id"].duplicated().any():
        errors.append("invoice_id not unique after dedup")
    for col in ["plan", "status", "billing_cycle", "currency", "region", "amount_local", "invoice_date"]:
        n = int(inv[col].isna().sum())
        if n:
            errors.append(f"{n} NULL {col} in invoices")
    if errors:
        raise ValidationError("; ".join(errors))


INVOICE_COLUMNS = [
    "invoice_id", "account_id", "account_name", "contact_email", "region", "industry", "plan",
    "billing_cycle", "seats", "currency", "amount_local", "fx_rate_to_usd", "amount_usd",
    "discount_pct", "status", "status_raw", "payment_method", "signup_date", "invoice_date",
    "invoice_year", "invoice_month", "churned", "csat_score", "support_tickets", "list_price_usd",
    "months_billed", "mrr_usd", "expected_amount_usd", "counts_as_revenue", "is_future_dated",
    "amount_mismatch", "status_overridden", "email_was_repaired", "email_invalid",
    "dedup_conflict_other_fields",
]


def run(s: Settings) -> dict:
    dq: Counter = Counter()
    raw = load_raw(s.raw_csv)
    dq["raw_rows"] = len(raw)
    norm = normalize(raw, dq)
    kept, rejected = dedup(norm, dq)
    inv = derive(kept, s, dq)[INVOICE_COLUMNS]
    validate(raw, inv, rejected)
    accounts = build_accounts(inv)
    dq["invoices"] = len(inv)
    dq["rejected"] = len(rejected)
    dq["accounts"] = len(accounts)

    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = s.db_path.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        for name, frame in [("invoices", inv), ("invoices_rejected", rejected), ("accounts", accounts)]:
            # pandas 3 'str' dtype isn't understood by duckdb<1.5 -> hand it plain objects.
            frame = frame.astype({c: object for c in frame.columns if pd.api.types.is_string_dtype(frame[c])})
            con.register("_df", frame)
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM _df")
            con.unregister("_df")
        # Proper types for money/dates (pandas object columns come through loosely).
        con.execute("""
            CREATE OR REPLACE TABLE invoices AS SELECT * REPLACE (
              CAST(amount_local AS DECIMAL(14,2)) AS amount_local,
              CAST(amount_usd AS DECIMAL(14,2)) AS amount_usd,
              CAST(signup_date AS DATE) AS signup_date,
              CAST(invoice_date AS DATE) AS invoice_date,
              CAST(invoice_year AS INTEGER) AS invoice_year,
              CAST(churned AS BOOLEAN) AS churned,
              CAST(csat_score AS INTEGER) AS csat_score,
              CAST(seats AS INTEGER) AS seats,
              CAST(support_tickets AS INTEGER) AS support_tickets
            ) FROM invoices""")
        con.execute("""
            CREATE OR REPLACE TABLE accounts AS SELECT * REPLACE (
              CAST(churned AS BOOLEAN) AS churned,
              CAST(csat_score AS INTEGER) AS csat_score,
              CAST(seats AS INTEGER) AS seats,
              CAST(net_revenue_usd AS DECIMAL(14,2)) AS net_revenue_usd,
              CAST(first_invoice_date AS DATE) AS first_invoice_date,
              CAST(last_invoice_date AS DATE) AS last_invoice_date
            ) FROM accounts""")
    finally:
        con.close()
    tmp.replace(s.db_path)  # atomic swap: readers never see a half-built warehouse

    report = {"fx_mode": s.fx_mode, "run_date": s.run_date.isoformat(), **dict(sorted(dq.items()))}
    (s.db_path.parent / "dq_report.json").write_text(json.dumps(report, indent=2))
    return report
