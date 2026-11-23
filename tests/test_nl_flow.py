"""NL layer control flow (spec 03) with a stub LLM: no network, no key.

These test OUR code around the model (validation, guardrails, repair loop, error surfacing),
not the model's SQL-writing ability — that's what `python -m evals.run` measures.
"""
import json
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.nl_query import NLQueryEngine
from evals.run import grade


def _resp(plan: dict | None = None, stop_reason: str = "end_turn"):
    text = json.dumps(plan) if plan is not None else ""
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class StubClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def plan(sql, answerable=True):
    return {"answerable": answerable, "interpretation": "test", "assumptions": [], "sql": sql}


@pytest.fixture
def engine_factory(built_db):
    engines = []

    def make(*responses):
        eng = NLQueryEngine(get_settings(), client=StubClient(*responses))
        engines.append(eng)
        return eng
    yield make
    for e in engines:
        e.close()


def test_happy_path_numbers_come_from_duckdb(engine_factory, cases):
    eng = engine_factory(_resp(plan(cases["q3_refunds_total"]["golden_sql"])))
    ans = eng.answer("How much have we given back in refunds?")
    assert ans.error is None and ans.rows and float(ans.rows[0][0]) > 0
    assert "LIMIT" in ans.sql  # guarded SQL is what's shown
    # schema card and business rules are in the (cached) system prompt
    system = eng.client.calls[0]["system"][0]
    assert "current_mrr_usd" in system["text"] and system["cache_control"] == {"type": "ephemeral"}


def test_repair_retry_once(engine_factory):
    eng = engine_factory(_resp(plan("SELECT no_such_col FROM invoices")),
                         _resp(plan("SELECT COUNT(*) FROM invoices")))
    ans = eng.answer("how many invoices?")
    assert ans.error is None and ans.rows[0][0] == 5000 and ans.llm_calls == 2
    assert "no_such_col" in eng.client.calls[1]["messages"][-1]["content"]


def test_repair_gives_up_after_one_retry(engine_factory):
    bad = plan("SELECT nope FROM invoices")
    ans = engine_factory(_resp(bad), _resp(bad)).answer("x")
    assert ans.error and ans.error.startswith("SQL error") and ans.llm_calls == 2


def test_guardrail_blocks_and_is_not_retried(engine_factory):
    ans = engine_factory(_resp(plan("DELETE FROM invoices"))).answer("delete stuff")
    assert ans.guardrail_rejected and not ans.rows and ans.llm_calls == 1


def test_unanswerable_runs_nothing(engine_factory):
    ans = engine_factory(_resp(plan("SELECT 1", answerable=False))).answer("What's our NPS?")
    assert not ans.answerable and ans.sql == "" and not ans.rows


def test_refusal_and_truncation(engine_factory):
    assert not engine_factory(_resp(None, "refusal")).answer("x").answerable
    assert "truncated" in engine_factory(_resp(None, "max_tokens")).answer("x").error


def test_input_validation(engine_factory):
    assert "empty" in engine_factory().answer("   ").error
    assert "longer" in engine_factory().answer("x" * 501).error


def test_grader_modes():
    A = lambda cols, rows: SimpleNamespace(answerable=True, guardrail_rejected=False, error=None, columns=cols, rows=rows, sql="", interpretation="")
    assert grade({"compare": "scalar"}, A(["v"], [(100.2,)]), ["v"], [(100.0,)])[0]
    assert not grade({"compare": "scalar"}, A(["v"], [(110,)]), ["v"], [(100.0,)])[0]
    assert grade({"compare": "keyed"}, A(["plan", "pct"], [("Enterprise", 25.0), ("starter", 20.0)]), ["plan", "r"], [("enterprise", 0.25), ("starter", 0.2)])[0]
    assert grade({"compare": "set", "key_column": "account_id"}, A(["account_name", "account_id"], [("a", "1"), ("b", "2")]), ["account_id"], [("2",), ("1",)])[0]
    assert grade({"compare": "row"}, A(["usd", "n"], [(500.0, 3)]), ["n", "usd"], [(3, 500.0)])[0]
