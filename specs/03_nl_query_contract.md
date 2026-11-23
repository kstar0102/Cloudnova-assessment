# Spec 03 — Natural-language query contract

## Interface
- CLI: `python -m app ask "<question>"` (and `python -m app demo` for the stakeholder set).
- Library: `app.nl_query.answer(question: str) -> Answer`.

## Flow
1. **Input validation:** question non-empty, ≤ 500 chars.
2. **LLM call** (`claude-opus-5-5` by default, `CLOUDNOVA_MODEL` to override) with:
   - system prompt = role + **schema card** (tables, columns, types, allowed values,
     generated from the live DuckDB catalog — never hand-maintained) + **metric
     definitions from spec 02** + worked SQL examples.
   - structured output (JSON schema): `{ "sql": str, "interpretation": str,
     "assumptions": [str], "answerable": bool }`.
   - `answerable=false` when the data can't answer (e.g. "pipeline next quarter",
     "NPS") → we return the model's explanation and run nothing.
3. **SQL guardrails** (all must pass, else the query is rejected and nothing runs):
   - parses as exactly **one** statement (sqlglot, DuckDB dialect);
   - statement is a `SELECT` (CTEs allowed); no DDL/DML/`PRAGMA`/`ATTACH`/`COPY`/`INSTALL`;
   - only tables `invoices`, `accounts` (plus CTE names) referenced; no table functions
     like `read_csv`;
   - an outer `LIMIT` (default 200) is applied if absent.
   - Defence in depth: DuckDB connection is opened `read_only=True`.
4. **Execute** → rows. On SQL error: **one** repair retry, sending the error back to the model.
5. **Return** `Answer{question, sql, interpretation, assumptions, columns, rows, error,
   latency_ms, input_tokens, output_tokens}`. CLI prints the interpretation, the SQL, and a table.

## Non-goals
- No free-text summarization of results by the LLM: numbers shown come **only** from DuckDB.
  This is the main trust guarantee — the model can pick the wrong query, but can't
  hallucinate a number. Showing the SQL lets a human audit the pick.
- No conversation memory.

## Errors
| Condition | Behaviour |
|---|---|
| No API key | Clear message: set `ANTHROPIC_API_KEY` in `.env`; `build`/`pytest` still work |
| Warehouse missing | Auto-run `build` first |
| API error (429/5xx) | SDK retries (2); then error surfaced, exit code 2 |
| Model refusal / unanswerable | Return explanation, no SQL executed |
| Guardrail rejection | Return reason + offending SQL, not executed |
