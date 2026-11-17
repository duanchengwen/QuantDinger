"""Nightly jobs cannot escape time bounds or interfere with paper processes."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location("securities_sync", Path(__file__).parents[1] / "securities_sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def test_timeout_removes_only_own_named_container(tmp_path):
    with patch.object(sync.ops, "compose", side_effect=lambda state, *args: list(args)), patch.object(sync.subprocess, "run") as run:
        run.side_effect = [subprocess.TimeoutExpired("provider", 300), SimpleNamespace(returncode=0)]
        assert sync.sync(tmp_path) == 1
    command = run.call_args_list[0].args[0]
    name = command[command.index("--name") + 1]
    assert name.startswith("quantdinger-securities-")
    assert run.call_args_list[1].args[0] == ["docker", "rm", "-f", name]
    assert "worker" in command
    assert "stop" not in command


def test_failure_not_reported_as_success(tmp_path):
    with patch.object(sync.ops, "compose", return_value=["isolated-job"]), patch.object(sync.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
        assert sync.sync(tmp_path) == 1


def test_existing_operation_lock_rejects_job(tmp_path):
    with sync.ops.operation_lock(tmp_path), pytest.raises(RuntimeError), patch.object(sync.ops, "compose", return_value=["job"]), patch.object(sync.subprocess, "run") as run:
        sync.sync(tmp_path)
    run.assert_not_called()


def test_timer_has_publication_delay_and_no_secrets(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "deployment.json").write_text(json.dumps({"project": "paper-test"}))
    with patch.object(sync.Path, "home", return_value=tmp_path), patch.object(sync.ops, "run"):
        sync.install_timer(state)
    units = tmp_path / ".config/systemd/user"
    timer = (units / "quantdinger-securities-paper-test.timer").read_text()
    service = (units / "quantdinger-securities-paper-test.service").read_text()
    assert "19:10:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer
    assert "TimeoutStartSec=6min" in service
    assert "PASSWORD" not in service
    with patch.object(sync.Path, "home", return_value=tmp_path), pytest.raises(ValueError):
        sync.install_timer(state)
