# Spec 01 — Cleaned schema & transformation rules

Status: **v1, written before implementation.** Inputs: `data/BUSINESS_CONTEXT.md`,
`docs/data_profile.md` (findings F1–F13). Output: DuckDB file `build/cloudnova.duckdb`.

## Pipeline order (order matters)
1. Read CSV with **every column as string**, no NA inference (blank ≠ "NA" region!).
2. Trim whitespace on every cell; blank → NULL.
3. Normalize categories (§C), parse types (§T).
4. Dedup (§D).
5. Derive (§V).
6. Build `accounts` snapshot table (§A) and write DQ report.

## §T — Types & parsing

| Column | Clean type | Rule |
|---|---|---|
| `invoice_id` | VARCHAR NOT NULL | as-is (trimmed) |
| `account_id` | VARCHAR NOT NULL | as-is |
| `account_name` | VARCHAR | as-is |
| `contact_email` | VARCHAR NULL | lower-case; `_at_` → `@` and `email_was_repaired = true` (F12); if still no single `@` → NULL + `email_invalid = true` |
| `region` | VARCHAR NOT NULL | upper-case; ∈ {NA, EMEA, APAC, LATAM} |
| `industry` | VARCHAR NULL | title-case; blank → NULL |
| `seats` | INTEGER NOT NULL | > 0 |
| `currency` | VARCHAR NOT NULL | upper-case; ∈ {USD, EUR, GBP} |
| `amount_local` | DECIMAL(14,2) | **Amount rule:** strip currency symbols (`$€£`), ISO codes (`USD/EUR/GBP`), commas, spaces; a `-` anywhere ⇒ negative; parse as decimal. `"31432"` ⇒ 31432.00 (missing cents are not ×100). Unparseable ⇒ NULL + `amount_unparseable` |
| `discount_pct` | DECIMAL(5,2) | 0 ≤ x < 100 |
| `signup_date`, `invoice_date` | DATE | **Date rule** (F3, F4): `YYYY-MM-DD`, `YYYY/MM/DD` ⇒ ISO; `MM/DD/YYYY` (slash) ⇒ **month-first**; `DD-MM-YYYY` (dash) ⇒ **day-first**; `Mon D YYYY` ⇒ named month. Any other shape or invalid calendar date ⇒ NULL + flag. Evidence: across the file the slash form never has month > 12 in position 1, and the dash form never has month > 12 in position 2, so a *per-format* convention is consistent with 100% of unambiguous rows. |
| `churned` | BOOLEAN | true ∈ {true, yes, y, 1}; false ∈ {false, no, n, 0} (case-insensitive); else NULL |
| `csat_score` | INTEGER NULL | 1–5, else NULL |
| `support_tickets` | INTEGER | ≥ 0 |

## §C — Category normalization (case- and whitespace-insensitive)

**plan** → `starter` | `pro` | `enterprise`
- starter: starter, start, tier 1
- pro: pro, professional, tier 2
- enterprise: enterprise, ent, tier 3

**billing_cycle** → `monthly` | `annual`
- monthly: monthly · annual: annual, annually, yearly

**status** → `paid` | `pending` | `failed` | `refunded` | `void` (table from BUSINESS_CONTEXT)
- paid: paid, complete, completed, success
- pending: pending, in_review, awaiting
- failed: failed, declined, error
- refunded: refunded, refund, charged_back
- void: void, cancelled, canceled

**Sign override (F8):** if `amount_local < 0` then `status = 'refunded'` regardless of label,
and `status_overridden = true`. Rationale: the money moved backwards; a label saying
"completed" on a negative amount is a source-system disagreement, and its twin row says refunded.

**payment_method** → `credit_card` | `ach` | `wire` | `invoice` | NULL
- credit_card: credit_card, credit card · ach: ach · wire: wire, wire transfer · invoice: invoice

Any value not in a mapping ⇒ NULL + counted in DQ report as `unmapped_<column>` (pipeline
does **not** fail; an unmapped `plan` or `status` *does* fail validation because metrics
depend on them).

## §D — Deduplication

- **D1 exact dups:** after §T/§C normalization, drop rows identical on all columns (excluding
  `status_raw`). *Amended after first build:* spec v1 predicted 86; actual is **109**, because
  27 of the 43 "conflicting" pairs only differed by status *spelling* (`paid` vs `completed`)
  and collapse once canonicalized. The profile measured raw strings; the spec rule is right,
  the prediction was wrong.
- **D2 conflicting dups (same `invoice_id`, different values):** 16 invoice_ids after D1
  (14 genuine: paid-vs-pending ×7, paid-vs-void ×5, paid-vs-failed ×2, ≈ **$168K** at stake;
  2 are the F8 refund pairs that differ only by `status_overridden`). Keep one row per
  `invoice_id` by **status precedence**:
  `refunded > paid > failed > void > pending`
  Rationale: rows only disagree on status (F2). A refund is the latest lifecycle event and
  is backed by a negative amount; `paid` is evidence money arrived and beats a
  `void/cancelled` from a system that may not have seen the payment; `pending` is the least
  final state. Losing rows are written to `invoices_rejected` with `reject_reason`, never
  deleted silently.
- If dup rows ever differ on anything other than status/payment_method (not observed),
  keep the precedence winner but set `dedup_conflict_other_fields = true`.

## §V — Derived invoice columns

| Column | Definition |
|---|---|
| `fx_rate_to_usd` | config `FX_RATES` (EUR 1.08, GBP 1.27, USD 1.0) |
| `amount_usd` | `FX_MODE=multiply` (default, as written in BUSINESS_CONTEXT): `amount_local × rate`; `FX_MODE=divide` (what the data generator evidently did, F7): `amount_local ÷ rate`. Rounded to cents. |
| `list_price_usd` | starter 49, pro 99, enterprise 299 |
| `months_billed` | monthly 1, annual 10 |
| `expected_amount_usd` | `list_price × seats × months_billed × (1 − discount_pct/100)` (sign follows amount) |
| `mrr_usd` | `list_price × seats × (1 − discount_pct/100)` — **from price list, never from `amount`**, so annual lump sums are never double counted and FX choice can't affect MRR |
| `counts_as_revenue` | `status IN ('paid','refunded')` (refunds net, negative) |
| `invoice_year`, `invoice_month` | from `invoice_date` |
| `is_future_dated` | `invoice_date > run date` (F5) — kept, flagged |
| `amount_mismatch` | `abs(amount_usd_divide − expected_amount_usd) > 1.00` — consistency check vs price list |

## §A — `accounts` table (one row per `account_id`)

Account attributes vary between invoices of the same account (F9), so:
- **Snapshot rule:** attributes (`account_name, region, industry, plan, billing_cycle, seats,
  discount_pct, churned, csat_score, contact_email`) come from the account's **latest invoice**
  by `invoice_date` (tie-break: highest `invoice_id`; NULL dates sort last).
- `current_mrr_usd` = snapshot invoice `mrr_usd` if `churned = false`, else 0.
- `is_active` = `churned = false`.
- `net_revenue_usd` = Σ `amount_usd` where `counts_as_revenue`, across all the account's invoices.
- `invoice_count`, `first_invoice_date`, `last_invoice_date`.

## Validation (pipeline fails loudly if violated)
- `invoice_id` unique in `invoices`.
- `plan`, `status`, `billing_cycle`, `currency`, `region` non-null.
- Row accounting: `raw = invoices + rejected` (exact).
