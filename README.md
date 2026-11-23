# CloudNova — ask the invoice ledger in plain English, and see why the answer is right

**Track B:** a spec-driven cleaning pipeline plus a natural-language → SQL query layer.

```
$ python -m app ask "How much have we given back in refunds?"
Interpretation: Total refunds: sum of all refunded invoice amounts in USD, reported as a positive number.
SQL:
  SELECT ROUND(-SUM(amount_usd), 2) AS refunds_usd FROM invoices WHERE status = 'refunded' LIMIT 200
Result:
  refunds_usd
  ------------
  8,925,163.83
```
*(Illustrative: the layout matches the real CLI and the number is the verified golden value, but the interpretation wording is an example. Live runs haven't happened yet; see "Status of verification".)*

## What it does (for the finance lead)
1. **Cleans the export** following written rules ([specs/](specs)): 18 plan spellings → 3 plans,
   25 statuses → 5, five date formats, amounts like `"1,200.00 EUR"`, duplicate invoices,
   repaired emails. Nothing is dropped silently. Every removed row is kept in
   `invoices_rejected` with a reason, and `build/dq_report.json` counts every fix.
2. **Pre-computes the business rules** once: USD amounts, "counts as revenue", MRR from the
   price list, churn, and an account-level snapshot.
3. **Answers questions** by having Claude write one SQL query over those clean tables. You
   always see **how it interpreted the question, the exact SQL, and the result**. The model
   never types a number itself; every figure comes from the database.

## Run it
```bash
uv venv && uv pip install -e ".[dev]"          # Python 3.11+
cp .env.example .env                            # add ANTHROPIC_API_KEY (only needed for ask/demo/evals)

python -m app demo                              # ← single demo command: build + the 6 stakeholder questions
python -m app ask "Which region has the highest average MRR per account?"
python -m app build                             # pipeline only, no key needed
pytest -q                                       # 102 tests, no key needed
python -m evals.run                             # live NL→SQL evals (needs key)
```
(With plain `pip`: `python -m venv .venv && .venv/bin/pip install -e ".[dev]"`.)

## The answers (golden values, verified twice)
Each value comes from the canonical SQL in [specs/02_metrics.md](specs/02_metrics.md). It is
cross-checked by `pytest` against [tests/reference.py](tests/reference.py), a separate,
standard-library-only re-implementation that shares no code with the pipeline.

| # | Question | Answer (FX as written in brief) | If FX is the other way ⚠️ |
|---|---|---|---|
| Q1 | Paid revenue, 2024 | **$32,686,371.17** | $29,595,624.38 |
| Q2 | Region with highest avg MRR/account | **APAC** ($6,147; NA $5,633, EMEA $5,328, LATAM $5,188) | same |
| Q3 | Refunds given back | **$8,925,163.83** | $8,200,645.87 |
| Q4 | Churn rate, Enterprise vs Starter | **21.9% vs 18.8%** | same |
| Q5 | Top 5 accounts by net revenue | ACC-7509 Stark Inc, ACC-1420 Tyrell, ACC-9946 Initech, ACC-1196 Pied Piper, ACC-5211 Oscorp. **None has CSAT ≤ 2** (Tyrell's CSAT is unknown) | **a completely different top 5**, incl. ACC-3142 Soylent Corp with **CSAT 2** |
| Q6 | Stuck in pending/failed | **894 invoices, $28,983,255.93 exposed** | 894, $26,458,551.07 |

### ⚠️ Decision needed from finance before trusting any $ figure (FX direction)
The brief says *"EUR→USD 1.08"*, i.e. multiply a EUR amount by 1.08 to get USD. But the export
itself shows **every** EUR/GBP invoice equals `USD price × 1.08` (or × 1.27):
a €52,920 invoice is exactly 100 Starter seats × $49 × 10 months × 1.08. Read that way, dividing
is correct. The two rules **cannot both be true**. The default follows the written rule, and
`CLOUDNOVA_FX_MODE=divide` switches the warehouse to the other interpretation. MRR and churn
don't depend on it. Revenue moves ~10% and the top-5 list changes completely. This is
eval case `x1_fx_direction`, which is **expected to fail** until finance decides.

## Specs (written first; see `git log`)
- [specs/01_cleaned_schema.md](specs/01_cleaned_schema.md): types, parsing, normalization, dedup, derived columns
- [specs/02_metrics.md](specs/02_metrics.md): revenue, refunds, MRR, churn, exposure definitions + stakeholder Q mapping
- [specs/03_nl_query_contract.md](specs/03_nl_query_contract.md): NL→SQL interface, guardrails, errors
- [specs/04_eval_spec.md](specs/04_eval_spec.md): two-layer evaluation
- Grounded in [docs/data_profile.md](docs/data_profile.md) (measured before the spec was written)

Architecture: [docs/architecture.md](docs/architecture.md) (Mermaid). How the AI was steered:
[CLAUDE.md](CLAUDE.md), [.claude/](.claude), [docs/ai-session-log.md](docs/ai-session-log.md).

## Key design decisions
| Decision | Why | Trade-off |
|---|---|---|
| **Business logic in the warehouse, not the prompt** | The model selects columns like `counts_as_revenue` instead of re-deriving rules each time, which leaves less room for error | Questions outside the pre-built concepts get weaker answers |
| **LLM writes SQL only; numbers come from DuckDB; SQL is always shown** | A wrong *query* is auditable; an invented *number* isn't | Output is a table, not a narrative |
| **Dates: per-format rule** (`a/b/yyyy` = month-first, `a-b-yyyy` = day-first) | Fits 100% of the rows that can be checked in each format | A system that switched convention mid-format would go undetected |
| **Duplicate invoices: status precedence** refunded > paid > failed > void > pending; a negative amount always means refunded | Only status differs between twins; `paid` is evidence money arrived | 14 invoices / ≈$168K hinge on this rule |
| **MRR from the list price**, not from invoice amounts | Avoids double-counting annual invoices, and doesn't depend on FX | Ignores off-list deals (none found: 0 amount/price mismatches) |
| **Accounts = snapshot of latest invoice** | Same `account_id` shows different names/regions/plans across invoices | See limitations |
| **Execution-based evals + independent reference** | Compares results, not SQL text, so any correct query passes. Two implementations agreeing is real evidence | Reference implementation costs extra code |
| pandas + DuckDB, no framework | 5K rows; a framework would add surface area without adding capability | Not built for 100M rows (nor needed) |
| Claude Opus 5.5, structured JSON output, effort `medium` | Strong SQL; the schema guarantees a parseable plan. Model and effort are env-configurable | Cost/latency per question (measured by `evals.run`) |

## Known limitations & failure modes (honest list)
1. **Account identity is unreliable in the source.** 976 of 3,815 account IDs carry more than one
   company name, and 774 more than one region. Every per-account answer (Q2, Q4, Q5) uses the
   *latest invoice's* attributes. If account IDs were re-used across companies, those answers
   mix customers. This needs a CRM master, not a cleverer rule.
2. **FX direction is unresolved** (above). Every $ total depends on it.
3. **No payment date.** Revenue is bucketed by invoice *issue* date, and annual invoices count
   in full in their month (cash-style, not accrual-style recognition).
4. **103 invoices are dated after today** (up to 2027). They are included in all-time totals
   and flagged as `is_future_dated`. The model is not told to exclude them.
5. **"Current" MRR only.** "What was MRR in March 2024?" has no correct answer in this model
   (no subscription start/end history). The model may still produce a plausible-looking query.
6. **Model ambiguity:** "revenue" without "paid" is mapped to *net* recognized revenue (paid −
   refunds). The interpretation line says so, but a hurried reader may miss it.
7. **CSAT is null for 623 invoices.** "Flag CSAT ≤ 2" can't flag unknowns (Tyrell in Q5).
8. **Guardrails are allow-list based and tested**, but SQL sandboxing isn't a security boundary
   against a determined attacker. The read-only connection with external access off is the real
   boundary.

### Status of verification
- ✅ Pipeline, metrics, guardrails, and NL control flow (stub LLM): **102 tests passing**.
- ⏳ **Live NL→SQL accuracy has not been run yet.** No API key was available while building.
  `python -m evals.run` is ready: 11 cases (Q1–Q6, 2 extra metrics, 2 refusal/guardrail cases,
  1 known failure), with per-case latency and token counts.

## Production hardening (bonus)
- **CI** ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs all 102 tests and builds the
  pipeline on every push with no secrets. It runs the live NL evals only if an
  `ANTHROPIC_API_KEY` repo secret exists.
- Also included: SQL guardrails + read-only DB, input validation, atomic warehouse swap,
  config in env, `.env` gitignored.

## With another day
1. Run the live evals, then grow them to ~40 questions phrased in finance language, including
   paraphrases of Q1–Q6, to measure robustness.
2. Get the FX and account-identity answers from finance; encode them as spec changes.
3. Add a `subscriptions` table (start/end per account) to support point-in-time MRR and churn
   over time.
4. A thin `POST /ask` (FastAPI) wrapper around `NLQueryEngine.answer`, plus a result cache
   keyed on (question, warehouse version).
5. Have the model flag *which* known caveat (FX, snapshot, future-dated) applies to each
   answer, driven from the schema card.
