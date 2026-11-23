"""Pipeline invariants (spec 01 'Validation' + §D) and golden metrics vs. independent reference."""
import pytest

from app.config import ROOT
from tests import reference as R

RAW = str(ROOT / "data" / "cloudnova_invoices.csv")


@pytest.fixture(scope="module")
def ref():
    rows = R.load(RAW, fx_mode="multiply")
    return rows, R.accounts(rows)


def scalar(con, sql):
    return float(con.execute(sql).fetchone()[0])


def test_row_accounting(built_db):
    con, report = built_db
    assert report["raw_rows"] == report["invoices"] + report["rejected"] == 5125
    assert report["invoices"] == 5000
    assert report["dedup_exact_removed"] == 109
    assert report["dedup_conflict_removed"] == 16


def test_invariants(built_db):
    con, report = built_db
    assert con.execute("SELECT COUNT(*) - COUNT(DISTINCT invoice_id) FROM invoices").fetchone()[0] == 0
    nulls = con.execute("SELECT COUNT(*) FROM invoices WHERE plan IS NULL OR status IS NULL OR invoice_date IS NULL OR amount_usd IS NULL").fetchone()[0]
    assert nulls == 0
    # every refund is negative, nothing else is (sign override, F8)
    assert con.execute("SELECT COUNT(*) FROM invoices WHERE (status='refunded') <> (amount_local < 0)").fetchone()[0] == 0
    # amounts agree with the price list under the data's own FX convention (F7)
    assert report["amount_mismatch_vs_price_list"] == 0
    for k, v in report.items():
        if k.startswith("unparsed_"):
            assert v == 0, k


def test_dedup_precedence(built_db):
    con, _ = built_db
    # INV-100630 appears as Complete and void -> paid wins (D2)
    assert con.execute("SELECT status FROM invoices WHERE invoice_id='INV-100630'").fetchone()[0] == "paid"
    # INV-102076 'completed' with negative amount + 'Refunded' twin -> refunded (F8)
    assert con.execute("SELECT status FROM invoices WHERE invoice_id='INV-102076'").fetchone()[0] == "refunded"


def test_q1_paid_2024(built_db, cases, ref):
    con, _ = built_db
    assert scalar(con, cases["q1_paid_revenue_2024"]["golden_sql"]) == pytest.approx(R.q1_paid_2024(ref[0]), abs=0.01)


def test_q2_region(built_db, cases, ref):
    con, _ = built_db
    got = {r[0]: r[1] for r in con.execute(cases["q2_region_highest_avg_mrr"]["golden_sql"]).fetchall()}
    exp = R.q2_region_avg_mrr(ref[1])
    assert got.keys() == exp.keys()
    for k in exp:
        assert got[k] == pytest.approx(exp[k], rel=1e-9)


def test_q3_refunds(built_db, cases, ref):
    con, _ = built_db
    assert scalar(con, cases["q3_refunds_total"]["golden_sql"]) == pytest.approx(R.q3_refunds(ref[0]), abs=0.01)


def test_q4_churn(built_db, cases, ref):
    con, _ = built_db
    got = dict(con.execute(cases["q4_churn_enterprise_vs_starter"]["golden_sql"]).fetchall())
    exp = R.q4_churn(ref[1])
    for plan in ("enterprise", "starter"):
        assert float(got[plan]) == pytest.approx(exp[plan], rel=1e-9)


def test_q5_top5(built_db, cases, ref):
    con, _ = built_db
    got = [r[0] for r in con.execute(cases["q5_top5_accounts"]["golden_sql"]).fetchall()]
    assert got == R.q5_top5(ref[1])


def test_q6_stuck(built_db, cases, ref):
    con, _ = built_db
    n, usd = con.execute(cases["q6_stuck_exposure"]["golden_sql"]).fetchone()
    exp_n, exp_usd = R.q6_stuck(ref[0])
    # Both sides round each row to cents, via different float paths (pandas vs. builtin round);
    # half-cent ties can land differently. 10 cents on ~$29M is noise, not logic.
    assert n == exp_n and float(usd) == pytest.approx(exp_usd, abs=0.10)
