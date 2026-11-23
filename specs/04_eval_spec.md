# Spec 04 — Evaluation

Two layers, because they fail for different reasons.

## Layer 1 — Pipeline & metric correctness (no API key, runs in CI)
`pytest -q` (`tests/`)
- Unit tests for every parsing rule in spec 01, fed with the literal dirty variants from
  the data profile (amount formats, date formats, status/plan spellings, booleans, emails).
- Pipeline invariants: row accounting (raw = kept + rejected), unique `invoice_id`,
  no NULL `plan/status`, expected dedup counts (86 exact, 43 conflict).
- **Golden metric values**: each stakeholder question's canonical SQL (spec 02) is run on
  the built warehouse and compared to a number computed by an **independent
  re-implementation** (`tests/reference.py`: plain-Python, row-by-row, written separately
  from the pandas pipeline). Agreement between two implementations is the evidence; a
  hard-coded number alone would only prove the code is stable.

## Layer 2 — NL→SQL answer accuracy (needs `ANTHROPIC_API_KEY`)
`python -m evals.run`
- Cases in `evals/cases.yaml`: `question`, `golden_sql`, `compare` mode, `expect` (pass|fail).
- Grading is **execution-based**: run the model's SQL and the golden SQL, compare result sets.
  - `scalar`: first cell of each, relative tolerance 0.5%.
  - `top_value`: first column of the first row matches (e.g. the winning region).
  - `set`: set of first-column values matches (e.g. top-5 account ids).
  - `refuse`: pass iff the system returns `answerable=false` or a guardrail rejection.
- At least one case is marked `expect: fail` with the reason — a known limitation, kept in
  the suite so we notice if it starts passing (or the reason changes).
- Output: pass/fail table, per-case latency and tokens, and `evals/results/<timestamp>.json`.
- Exit code non-zero if any `expect: pass` case fails.
