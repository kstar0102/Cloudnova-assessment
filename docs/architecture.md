# Architecture

Matches the code as built (module names in brackets).

```mermaid
flowchart LR
    subgraph Raw
        CSV[data/cloudnova_invoices.csv<br/>5,125 dirty rows]
    end

    subgraph Pipeline["Pipeline — python -m app build  [app/pipeline.py]"]
        L[load_raw<br/>all strings, blank→NULL]
        N[normalize<br/>parsers.py: amounts, dates,<br/>status/plan/cycle maps,<br/>sign override]
        D[dedup<br/>D1 exact → D2 status precedence]
        V[derive<br/>amount_usd FX_MODE, mrr_usd,<br/>counts_as_revenue, flags]
        A[build_accounts<br/>latest-invoice snapshot]
        VAL{validate<br/>row accounting,<br/>uniqueness, NOT NULL}
        L --> N --> D --> V --> A --> VAL
    end

    subgraph Store["DuckDB  build/cloudnova.duckdb"]
        T1[(invoices<br/>5,000)]
        T2[(accounts<br/>3,815)]
        T3[(invoices_rejected<br/>125 + reason)]
        DQ[/build/dq_report.json/]
    end

    subgraph Ask["Query — python -m app ask / demo  [app/nl_query.py]"]
        Q[question] --> IV[input validation]
        IV --> SC[system prompt =<br/>rules + schema card<br/>semantic.py, from live catalog]
        SC --> LLM[[Claude claude-opus-5-5<br/>structured output:<br/>answerable, sql, interpretation]]
        LLM --> G{guardrails.py<br/>sqlglot: 1 SELECT,<br/>allow-listed tables,<br/>no file/env fns, LIMIT}
        G -- rejected --> OUT
        G --> EX[execute on read-only conn<br/>external access off]
        EX -- SQL error, once --> LLM
        EX --> OUT[Answer: interpretation +<br/>SQL + rows from DuckDB]
    end

    subgraph Evals
        PT[pytest: parsers, invariants,<br/>golden SQL vs tests/reference.py,<br/>guardrails, stub-LLM flow]
        ER[evals/run.py: execution-based<br/>NL→SQL grading vs golden SQL]
    end

    CSV --> L
    VAL --> T1 & T2 & T3 & DQ
    T1 & T2 --> EX
    T1 & T2 --> SC
    T1 & T2 -.-> PT
    CSV -.-> PT
    OUT -.-> ER
```

**Trust boundary:** the LLM never produces a number. It produces SQL, which is checked and
executed against data that business rules have already been applied to. Every number the user
sees comes from DuckDB.
