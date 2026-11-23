import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def built_db(tmp_path_factory):
    """Build a fresh warehouse into a temp dir with a fixed run date (deterministic)."""
    out = tmp_path_factory.mktemp("build")
    os.environ["CLOUDNOVA_DB_PATH"] = str(out / "test.duckdb")
    os.environ["CLOUDNOVA_RUN_DATE"] = "2026-11-20"
    os.environ["CLOUDNOVA_FX_MODE"] = "multiply"
    from app import pipeline
    from app.config import connect_readonly, get_settings

    s = get_settings()
    report = pipeline.run(s)
    con = connect_readonly(s.db_path)
    yield con, report
    con.close()


@pytest.fixture(scope="session")
def cases():
    return {c["id"]: c for c in yaml.safe_load((ROOT / "evals" / "cases.yaml").read_text())}
