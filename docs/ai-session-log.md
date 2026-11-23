# AI session log (trimmed)

Tool: **Claude Code** (Claude Opus 5.5) in VS Code, one session, 2026-11-20.
Steering: [`CLAUDE.md`](../CLAUDE.md) and [`.claude/`](../.claude). The git history follows the
same order as this log.

## 1. Brief → track choice
- **Human:** "Open the docx in the folder and learn what to do. There are 2 tracks; I'll pick one."
- **Claude:** summarized both tracks and recommended **B**: 5 of 6 stakeholder questions are
  aggregations, which RAG answers badly and SQL answers exactly, and exact answers make the
  evals deterministic. It also asked about the API key and `git init`.
- **Human:** "ok, let me pick B"

## 2. Profile before spec (no code yet)
Claude profiled the CSV before writing any spec, so the rules are based on measurements.
Findings → [`docs/data_profile.md`](data_profile.md). The key ones, and the judgment applied to each:
- **FX contradiction (F7).** Amounts satisfy `local = USD × rate` on 5,079/5,125 rows, but the
  brief says to multiply local by the rate to get USD. *Decision:* don't silently "fix" the
  customer's written rule, and don't silently follow it either. Implemented `FX_MODE`, defaulted
  to the written rule, and added an eval case that **fails on purpose** to keep the issue visible.
- **"Ambiguous" dates aren't ambiguous per format (F3/F4).** The dash format is never
  month-first and the slash format is never day-first, so a per-format rule fits 100% of the
  rows that can be checked. That's better than guessing per value.
- **Account attributes unstable (F9).** This led to the latest-invoice snapshot rule, flagged
  as the biggest modeling caveat.

## 3. Spec → steering → build
- `CLAUDE.md` rule 2 ("business rules live in the warehouse, not the prompt") shaped the
  design: the LLM gets columns like `counts_as_revenue` and `current_mrr_usd` instead of rules
  to reapply on every question.
- Specs 01–04 were committed **before** any implementation (`git log`).

## 4. Where Claude's first attempt was wrong, and what was done about it
| What happened | Response |
|---|---|
| Spec v1 predicted 86 exact dups; the pipeline found 109 | Root cause: status canonicalization collapses spelling-only pairs. Amended the **spec** in its own commit, with the reason. Didn't bend the code to hit the old number. |
| `duckdb` latest wheel crashed (`Illegal instruction`) | Diagnosed a missing AVX2 instruction set on the build CPU, bisected versions, pinned `<1.5` with a comment. |
| pandas 3 changed missing strings to NaN, breaking the parsers | Made the parsers accept any type and treat non-strings as missing. Added a NaN case to the tests. |
| Golden Q6 off by $0.02 vs. reference | Identified per-row float rounding noise, not a logic bug. Set an explicit, commented tolerance. |
| Dedup took 94 s (per-group Python lambda) | Profiled it, then vectorized it over the duplicated ids only (now under 1 s). |
| Stub-LLM tests exposed that the eval runner would crash | DuckDB rejects two connections to one file with different configs. Added a single `connect_readonly()` helper. **Caught by tests before any live run.** |

## 5. Choices deliberately not taken
- **No LLM-written prose answers.** Numbers come only from DuckDB, so the model can choose
  the wrong query but can't invent a figure.
- **No server-side refusal-fallback beta.** It couldn't be exercised without a key, and an
  untested beta parameter on the only request path seemed riskier than it was worth.
- **No RAG/vector store.** Off-track for B and wrong for aggregations.

## 6. What was NOT verified
No `ANTHROPIC_API_KEY` was available in the build environment. The NL layer's control flow is
tested with a stub client, but **live NL→SQL accuracy (`python -m evals.run`) has not been run
yet.** Run it first; the eval harness prints per-case pass/fail, latency and tokens.
