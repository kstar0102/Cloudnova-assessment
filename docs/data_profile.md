# Data profile — `data/cloudnova_invoices.csv` (done before writing the spec)

Raw: **5,125 rows × 19 columns**, 5,000 distinct `invoice_id`, 3,815 distinct `account_id`.
Everything below was measured, not assumed; the spec rules in `specs/01_cleaned_schema.md`
cite these findings.

| # | Finding | Evidence | Spec rule it drives |
|---|---------|----------|---------------------|
| F1 | 82 exact-duplicate rows; **86** once values are trimmed + lower-cased | `df.duplicated()` vs normalized | Dedup step 1 runs *after* normalization |
| F2 | 43 `invoice_id`s appear twice with **only `status` differing** (e.g. `paid` vs `void`, `completed` vs `cancelled`) | groupby nunique: only `status` varies | Status precedence rule (D2) |
| F3 | `DD-MM-YYYY` (dash) format: 151/239 rows have day > 12 in position 1, **0** have month > 12 → dash format is day-first | regex + range check | Date rule: dash ⇒ day-first |
| F4 | `MM/DD/YYYY` (slash) format: 786 rows have value > 12 in position 2, **0** in position 1 → slash is month-first | same | Date rule: slash ⇒ month-first |
| F5 | Invoice dates span 2021-2027; 63 rows in 2027, **103 after the run date (2026-11-20)** once Nov/Dec 2026 are included | year extraction | `is_future_dated` flag; kept, not dropped |
| F6 | Amount formats: `4900.00`, `$4,900.00`, `4900`, `4,900.00 USD`, `€999.99`, `£9,999.99`, `9,999.99 EUR`, negatives `$-4,900.00` / `-1960.00` | shape profile | Amount parser (strip symbols/codes/commas, keep sign) |
| F7 | **FX direction contradiction.** 5,079/5,125 rows satisfy `amount_local = list_price × seats × months × (1−disc) × FX` exactly (rest differ by dropped cents). I.e. the generator *multiplied* USD by 1.08/1.27 to get EUR/GBP. BUSINESS_CONTEXT says "EUR→USD 1.08" (multiply local by 1.08 to get USD). Both can't be true. | ratio check | Configurable `FX_MODE`; default follows the written rule; open question #1 |
| F8 | Every negative amount carries a refund status **except 2** (`INV-102076` "completed", `INV-102708` "success") — and both are F2 near-dupes whose twin says Refunded/REFUND | filter | Sign beats label: negative ⇒ `refunded` |
| F9 | Account attributes are **not stable per `account_id`**: 976 accounts have >1 `account_name`, 774 >1 region, 930 >1 plan, 860 >1 `churned` value | groupby nunique | Account snapshot = latest invoice (D4); big caveat |
| F10 | Category variants: plan 18 spellings (incl. `Tier 1/2/3`, `Start`, `ENT`, padded whitespace); status 25; billing_cycle 6; payment_method 7 + blank; churned 10 | value_counts | Mapping tables in spec |
| F11 | Nulls: `contact_email` blank 292, `industry` blank 422, `csat_score` blank 623, `payment_method` blank 637 | value_counts | Nullable columns |
| F12 | Malformed emails: `billing_at_globex.com` pattern | regex | Repair `_at_`→`@`, flag `email_was_repaired` |
| F13 | `seats` ∈ {1,3,5,10,25,50,100,250}; `discount_pct` ∈ {0,10,15,20,25}; `csat_score` 1–5 | value_counts | Range constraints |
