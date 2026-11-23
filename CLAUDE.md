# CLAUDE.md — steering for AI-assisted work in this repo

Project: CloudNova invoice ledger → cleaned DuckDB warehouse → natural-language question
interface. Track B of the FDE take-home.

## Ground rules (read before writing code)

1. **Specs are the source of truth.** Every transformation must trace to a rule in
   `specs/`. If code and spec disagree, the spec wins — or change the spec *first*
   in a separate commit, with the reason. Never invent business logic in code.
   - `specs/01_cleaned_schema.md` — columns, types, parsing/normalization/dedup rules
   - `specs/02_metrics.md` — revenue, refunds, MRR, churn, exposure definitions
   - `specs/03_nl_query_contract.md` — NL→SQL interface, guardrails, output contract
   - `specs/04_eval_spec.md` — eval cases and how they are graded
2. **Business rules live in the warehouse, not in the LLM prompt.** The LLM writes
   SQL against pre-computed columns (`amount_usd`, `counts_as_revenue`, `mrr_usd`,
   `churned`...). It must never re-derive FX, status mapping, or MRR itself.
3. **Never silently drop rows.** Every row removed or altered is counted in the
   pipeline's data-quality report (`build/dq_report.json`).
4. **Unknown is not zero.** Unparseable values become NULL + a DQ flag, never 0 or a guess.
5. **Config over hardcoding.** FX rates, list prices, model id, paths → `app/config.py`
   (env-overridable). No secrets in code; `ANTHROPIC_API_KEY` comes from `.env`.
6. **Deterministic first.** Pipeline + metric tests must run with no API key.
   Only the NL layer needs the network.

## Stack
Python 3.12, pandas (cleaning), DuckDB (storage + query), sqlglot (SQL guardrails),
Anthropic SDK (`claude-opus-5-5`, structured outputs) for NL→SQL. `uv` for envs.

## Commands
- Setup: `uv venv && uv pip install -e .`
- Build warehouse: `python -m app build`
- Ask: `python -m app ask "What was total paid revenue in 2024?"`
- Demo (build + stakeholder questions): `python -m app demo`
- Tests (no key): `pytest -q`
- Evals: `python -m evals.run` (NL cases skipped without a key)

## Style
Small pure functions per parsing rule, each unit-tested against the dirty variants
seen in the data profile. Type hints. No notebooks. Keep commits small and spec-first.
