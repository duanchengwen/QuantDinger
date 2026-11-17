"""Operator CLI acceptance against a real PostgreSQL database."""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from app.ashare.runtime import claim_worker


def test_cli_migrate_initialize_and_restart_status(tmp_path):
    dsn = os.getenv("TEST_ASHARE_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_ASHARE_DATABASE_URL required")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"mode": "paper", "tenant_id": "cli-" + uuid.uuid4().hex,
                                 "account_id": "paper", "symbols": ["sz.000001"], "initial_cash": "10000"}))
    environment = {**os.environ, "ASHARE_DATABASE_URL": dsn}
    environment.pop("PYTHONPATH", None)
    command = [sys.executable, "-m", "app.ashare", "--config", str(config), "--output", str(tmp_path / "reports")]
    for action in ("migrate", "init", "status", "report"):
        process = subprocess.run(command + [action], env=environment, capture_output=True, text=True,
                                 cwd=Path(__file__).parents[2], timeout=20)
        assert process.returncode == 0, process.stdout + process.stderr
        result = json.loads(process.stdout)
        if action in ("status", "report"):
            assert result["account"]["cash"] == "10000.00"
            assert result["orders"] == []
    assert len(list((tmp_path / "reports" / "daily").glob("*.json"))) == 1


def test_only_one_worker_can_own_account_and_disconnect_releases():
    dsn = os.getenv("TEST_ASHARE_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_ASHARE_DATABASE_URL required")
    import psycopg2
    config = {"tenant_id": uuid.uuid4().hex, "account_id": "worker"}
    first, second = psycopg2.connect(dsn), psycopg2.connect(dsn)
    try:
        claim_worker(first, config)
        with pytest.raises(ValueError, match="already owns"):
            claim_worker(second, config)
        first.close()
        # Socket close is asynchronous at the server: assert eventual release,
        # not that PostgreSQL has already processed Terminate on the next call.
        deadline = time.monotonic() + 2
        while True:
            try:
                claim_worker(second, config)
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
    finally:
        first.close()
        second.close()
