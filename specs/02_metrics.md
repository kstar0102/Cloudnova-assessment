# Spec 02 — Metric definitions (the semantic layer)

These definitions are what "correct" means. Each has a **canonical SQL** that the eval
harness uses as ground truth and that is given to the LLM as worked examples.
Tables: `invoices` (deduped, one row per invoice) and `accounts` (one row per account).

| Metric | Definition | Canonical SQL |
|---|---|---|
| **Paid revenue** (gross) | Σ `amount_usd` where `status='paid'` | `SELECT SUM(amount_usd) FROM invoices WHERE status='paid'` |
| **Net recognized revenue** | Σ `amount_usd` where `counts_as_revenue` (paid + negative refunds) | `... WHERE counts_as_revenue` |
| **Refunds given back** | Σ `-amount_usd` where `status='refunded'` (reported positive) | `SELECT -SUM(amount_usd) FROM invoices WHERE status='refunded'` |
| **MRR (account)** | `accounts.current_mrr_usd` — list-price based, 0 for churned | — |
| **Avg MRR per account** | `AVG(current_mrr_usd)` over **active** accounts (`is_active`) | grouped by dimension |
| **Churn rate** | `COUNT(*) FILTER (WHERE churned) / COUNT(*)` over accounts with non-null `churned`, plan from snapshot | |
| **Exposed $ (stuck)** | count and Σ `amount_usd` where `status IN ('pending','failed')` | |
| **Time bucketing** | by `invoice_date` (issue date — we have no payment date) | `invoice_year = 2024` |

## Stakeholder questions → definitions used

| # | Question | Interpretation (stated in the answer) |
|---|---|---|
| Q1 | Total recognized revenue in USD for **paid** invoices in 2024 | Paid revenue, `invoice_year=2024`. Also report net-of-refunds as context. |
| Q2 | Region with highest **average MRR per account** | Avg `current_mrr_usd` over active accounts, by snapshot `region` |
| Q3 | How much given back in refunds | Refunds given back, all time, USD |
| Q4 | Churn rate Enterprise vs Starter | Churn rate by snapshot `plan` |
| Q5 | Top 5 accounts by revenue, flag CSAT ≤ 2 | Rank by `net_revenue_usd`; `csat_score <= 2` from snapshot |
| Q6 | Invoices stuck in pending/failed + exposed $ | Exposed $ (stuck), all time |

## Deliberate choices (and their alternatives)
- **Issue date, not cash date.** There is no `paid_at`; revenue is bucketed by `invoice_date`.
- **Annual invoices recognized in full in the invoice month** (cash-basis view). True
  accrual recognition (spreading over 12 months) is out of scope; called out in README.
- **MRR ignores invoice status.** An account's MRR is its subscription's run-rate; a single
  failed invoice doesn't zero it. Churn is what zeroes it.
- **Future-dated invoices are included** in all-time totals but are visible via
  `is_future_dated` (63 rows).
