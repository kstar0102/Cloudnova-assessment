"""All tunables in one place (spec 01 §V, spec 03). Env-overridable; no secrets here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LIST_PRICE_USD = {"starter": 49.0, "pro": 99.0, "enterprise": 299.0}
MONTHS_BILLED = {"monthly": 1, "annual": 10}  # annual bills 10 months (2 free)
FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}


@dataclass(frozen=True)
class Settings:
    raw_csv: Path = field(default_factory=lambda: ROOT / os.getenv("CLOUDNOVA_RAW_CSV", "data/cloudnova_invoices.csv"))
    db_path: Path = field(default_factory=lambda: ROOT / os.getenv("CLOUDNOVA_DB_PATH", "build/cloudnova.duckdb"))
    fx_mode: str = field(default_factory=lambda: os.getenv("CLOUDNOVA_FX_MODE", "multiply"))
    run_date: date = field(default_factory=lambda: date.fromisoformat(os.getenv("CLOUDNOVA_RUN_DATE", date.today().isoformat())))
    model: str = field(default_factory=lambda: os.getenv("CLOUDNOVA_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: os.getenv("CLOUDNOVA_EFFORT", "medium"))
    max_question_chars: int = 500
    default_row_limit: int = 200

    def __post_init__(self) -> None:
        if self.fx_mode not in ("multiply", "divide"):
            raise ValueError(f"CLOUDNOVA_FX_MODE must be 'multiply' or 'divide', got {self.fx_mode!r}")


def connect_readonly(db_path: Path):
    """The one way to open the warehouse for querying. DuckDB refuses a second connection to the
    same file with a different config in one process, so everything must share this config."""
    import duckdb

    return duckdb.connect(str(db_path), read_only=True, config={"enable_external_access": False})


def get_settings() -> Settings:
    return Settings()
