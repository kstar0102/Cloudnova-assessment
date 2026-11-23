"""Natural-language question -> guarded SQL -> DuckDB rows (spec 03).

Trust model: the LLM only *chooses a query*. Every number shown comes from DuckDB, and the
SQL is always shown so a human can audit the choice.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import anthropic
import duckdb
from pydantic import BaseModel, ValidationError

from app.config import Settings, connect_readonly
from app.guardrails import GuardrailError, check_and_limit
from app.semantic import schema_card


class QueryPlan(BaseModel):
    answerable: bool
    interpretation: str
    assumptions: list[str]
    sql: str


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "interpretation": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "sql": {"type": "string"},
    },
    "required": ["answerable", "interpretation", "assumptions", "sql"],
    "additionalProperties": False,
}

SYSTEM_TEMPLATE = """You translate questions from CloudNova's finance team into one DuckDB SQL query \
over a cleaned invoice warehouse. Your SQL is executed read-only and its result is shown to the \
user verbatim, next to your SQL and interpretation, so the query must answer exactly the question asked.

<schema>
{schema}
</schema>

<business_rules>
The business logic is already computed into columns. Use those columns; do not re-derive it.
- Money: always aggregate amount_usd (already converted to USD). Never amount_local.
- Revenue: "revenue for paid invoices" / "paid revenue" = SUM(amount_usd) WHERE status = 'paid'. \
"Recognized revenue" in general, "net revenue" or "revenue after refunds" = SUM(amount_usd) WHERE counts_as_revenue. \
If the question says "paid", use status = 'paid'. pending, failed and void are never revenue.
- Refunds "given back": -SUM(amount_usd) WHERE status = 'refunded' (report as a positive number).
- MRR: use accounts.current_mrr_usd (0 for churned accounts). "Average MRR per account" = \
AVG(current_mrr_usd) over accounts WHERE is_active. Never sum invoice amounts to get MRR.
- Churn rate: from the accounts table, AVG(CASE WHEN churned THEN 1.0 ELSE 0.0 END) WHERE churned IS NOT NULL, \
as a fraction 0-1, grouped by the requested dimension.
- Account-level questions (by region, plan, top accounts, CSAT) use the accounts table; \
"top accounts by revenue" ranks by accounts.net_revenue_usd.
- Time: filter by invoice_year / invoice_month / invoice_date (issue date).
- "Stuck" invoices = status IN ('pending', 'failed'); exposed $ = SUM(amount_usd) of those.
</business_rules>

<output_rules>
- One SELECT statement (CTEs allowed) over tables invoices and accounts only.
- Put the identifying column first (e.g. region, plan, account_id), then the measures. \
Include account_id and account_name when listing accounts.
- Round money to 2 decimals. Name columns clearly (e.g. paid_revenue_usd, churn_rate).
- Order results so the answer to the question is in the first row when the question asks for a "highest"/"top".
- interpretation: one sentence saying which definition you applied, in finance language.
- assumptions: any choice you made that the user might disagree with (empty list if none).
- If the question asks to modify data, or needs data that is not in the schema (e.g. NPS, forecasts, \
pipeline, costs), set answerable=false, explain why in interpretation, and set sql to "".
</output_rules>"""


@dataclass
class Answer:
    question: str
    answerable: bool = True
    interpretation: str = ""
    assumptions: list[str] = field(default_factory=list)
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    error: str | None = None
    guardrail_rejected: bool = False
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rows"] = [[_jsonable(v) for v in r] for r in self.rows]
        return d


def _jsonable(v: Any) -> Any:
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return float(v) if hasattr(v, "__float__") else str(v)


class NLQueryEngine:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        self.s = settings
        self.con = connect_readonly(settings.db_path)
        self.system = SYSTEM_TEMPLATE.format(schema=schema_card(self.con))
        self.client = client or anthropic.Anthropic()

    def close(self) -> None:
        self.con.close()

    def _plan(self, messages: list[dict], ans: Answer) -> QueryPlan:
        resp = self.client.messages.create(
            model=self.s.model,
            max_tokens=8000,
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={"effort": self.s.effort,
                           "format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
        )
        ans.llm_calls += 1
        ans.input_tokens += resp.usage.input_tokens
        ans.output_tokens += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            return QueryPlan(answerable=False, interpretation="The model declined this request.", assumptions=[], sql="")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("model output was truncated (max_tokens)")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return QueryPlan.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as e:
            raise RuntimeError(f"model returned an invalid plan: {e}") from e

    def answer(self, question: str) -> Answer:
        ans = Answer(question=question)
        t0 = time.perf_counter()
        try:
            question = (question or "").strip()
            if not question:
                raise ValueError("question is empty")
            if len(question) > self.s.max_question_chars:
                raise ValueError(f"question longer than {self.s.max_question_chars} characters")

            messages: list[dict] = [{"role": "user", "content": question}]
            plan = self._plan(messages, ans)
            for attempt in range(2):  # initial try + one repair (spec 03 step 4)
                ans.answerable, ans.interpretation, ans.assumptions = plan.answerable, plan.interpretation, plan.assumptions
                ans.sql = plan.sql
                if not plan.answerable:
                    ans.sql = ""
                    return ans
                try:
                    safe_sql = check_and_limit(plan.sql, self.s.default_row_limit)
                except GuardrailError as e:
                    ans.guardrail_rejected = True
                    ans.error = f"guardrail rejected SQL: {e}"
                    return ans  # never repair around a guardrail
                try:
                    cur = self.con.execute(safe_sql)
                    ans.sql = safe_sql
                    ans.columns = [d[0] for d in cur.description]
                    ans.rows = cur.fetchall()
                    ans.error = None
                    return ans
                except duckdb.Error as e:
                    ans.error = f"SQL error: {e}"
                    if attempt == 1:
                        return ans
                    messages += [
                        {"role": "assistant", "content": plan.model_dump_json()},
                        {"role": "user", "content": f"That SQL failed in DuckDB with: {e}\nReturn a corrected plan."},
                    ]
                    plan = self._plan(messages, ans)
            return ans
        except anthropic.AuthenticationError:
            ans.error = "Anthropic authentication failed: set ANTHROPIC_API_KEY in .env (see .env.example)."
        except anthropic.RateLimitError:
            ans.error = "Anthropic rate limit hit after retries; try again shortly."
        except anthropic.APIStatusError as e:
            ans.error = f"Anthropic API error {e.status_code}: {e.message}"
        except anthropic.APIConnectionError:
            ans.error = "Could not reach the Anthropic API (network)."
        except (ValueError, RuntimeError) as e:
            ans.error = str(e)
        finally:
            ans.latency_ms = int((time.perf_counter() - t0) * 1000)
        return ans
