#!/usr/bin/env python3
"""One bounded current-theme observation per configured board and CST evening.

Only install-timer initializes the private journal. Missing or corrupt journals
fail closed: restore them rather than guessing whether an interrupted fetch ran.
No historical date, caller clock, source URL or alternative board is accepted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

import paper_stack as ops

CONFIG_VERSION = "eastmoney-configured-themes-20261118-v1"
CONFIGURED_THEMES = (("BK0800", "人工智能"), ("BK1090", "机器人概念"), ("BK0493", "新能源"))
STATE_NAME = "theme-sync-state.json"
MARKER_NAME = ".theme-scheduler-initialized"
SHANGHAI = ZoneInfo("Asia/Shanghai")
JOB_TIMEOUT = 45
CLEANUP_TIMEOUT = 10
TOTAL_TIMEOUT = 240
MAX_OUTPUT_BYTES = 16384
MAX_STATE_BYTES = 65536

# This runs inside the existing worker image. Snapshot verification uses the
# production ThemeStore, including manifest/page hashes and stored constituents.
# The payload supplies neither a date nor an evidence path/source endpoint.
CONTAINER_SCRIPT = r'''
import json
import sys
from contextlib import closing
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
import psycopg2
from app.ashare.schema import get_database_url
from app.ashare.themes import ThemeStore, ingest_theme

scope = {"BK0800": "人工智能", "BK1090": "机器人概念", "BK0493": "新能源"}
request = json.loads(sys.stdin.read(4096))
board, name = request.get("board_code"), request.get("board_name")
if set(request) != {"board_code", "board_name", "allow_ingest"} or scope.get(board) != name or type(request["allow_ingest"]) is not bool:
    print(json.dumps({"status": "failed", "error": "invalid_theme_job_scope"}))
    raise SystemExit(1)
zone = ZoneInfo("Asia/Shanghai")
now = datetime.now(timezone.utc)
local = now.astimezone(zone)
start = datetime.combine(local.date(), time(20, 10), tzinfo=zone)
base = {"board_code": board, "board_name": name, "window_date": local.date().isoformat(), "attempted_network": False}

def receipt_summary(snapshot, current):
    if snapshot.get("status") != "observed":
        return None
    received = datetime.fromisoformat(snapshot["received_at"])
    if (snapshot.get("board_code") != board or snapshot.get("board_name") != name
            or received.utcoffset() is None or received > current
            or snapshot.get("source") != "eastmoney"
            or snapshot.get("membership_semantics") != "current_observation"
            or snapshot.get("published_at") is not None
            or snapshot.get("historical_membership_certified") is not False):
        raise ValueError("receipt_scope_rejected")
    if received < start or received.astimezone(zone).date() != local.date():
        return None
    keys = ("receipt_id", "received_at", "source_hash", "member_count")
    return {**{key: snapshot[key] for key in keys}, "evidence_status": "verified"}

result, phase = None, "theme_receipt_verification_failed"
try:
    if local < start:
        result = {**base, "status": "skipped_before_window", "error": "window_not_open"}
    else:
        with closing(psycopg2.connect(get_database_url(), connect_timeout=5,
                options="-c statement_timeout=5000 -c lock_timeout=2000")) as connection:
            store = ThemeStore(connection)
            snapshot = store.snapshot(board, limit=1)
            verified = receipt_summary(snapshot, datetime.now(timezone.utc))
            if verified:
                result = {**base, **verified, "status": "skip_already_observed"}
            elif not request["allow_ingest"]:
                result = {**base, "status": "skip_attempted", "error": "window_attempt_already_recorded"}
            else:
                phase = "theme_ingest_failed"
                base["attempted_network"] = True
                receipt = ingest_theme(connection, "/app/data/evidence/themes", board_code=board,
                                       expected_name=name, timeout=30)
                phase = "theme_post_ingest_verification_failed"
                verified = receipt_summary(store.snapshot(board, limit=1), datetime.now(timezone.utc))
                if not verified or verified["receipt_id"] != receipt["receipt_id"]:
                    raise ValueError("receipt_verification_failed")
                result = {**base, **verified, "status": "ingested"}
except Exception:
    result = {**base, "status": "failed", "error": phase}
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(0 if result["status"] in {"ingested", "skip_already_observed"} else 1)
'''


def utc_now():
    return datetime.now(UTC)


def window(now):
    if now.utcoffset() is None:
        raise ValueError("aware_clock_required")
    local = now.astimezone(SHANGHAI)
    start = datetime.combine(local.date(), clock_time(20, 10), tzinfo=SHANGHAI)
    return local.date().isoformat(), start


def atomic_state(path, value):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError("theme_journal_missing_or_invalid")
    try:
        result = json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError):
        raise ValueError("theme_journal_missing_or_invalid") from None
    if not isinstance(result, dict):
        raise ValueError("theme_journal_missing_or_invalid")  # noqa: TRY004 - serialized journal validation uses ValueError.
    return result


def project_name(state):
    project = json.loads((state / "deployment.json").read_text())["project"]
    ops.check_project(project)
    return project


def load_journal(state):
    marker, journal = read_json(state / MARKER_NAME), read_json(state / STATE_NAME)
    project = project_name(state)
    for value in (marker, journal):
        if (value.get("schema_version") != 1 or value.get("config_version") != CONFIG_VERSION
                or value.get("project") != project
                or not isinstance(value.get("scheduler_id"), str)
                or not re.fullmatch(r"[a-f0-9]{32}", value["scheduler_id"])):
            raise ValueError("theme_journal_scope_invalid")
    if journal.get("mode") != "paper" or journal.get("approvals_created") != 0:
        raise ValueError("theme_journal_scope_invalid")
    if marker["scheduler_id"] != journal["scheduler_id"]:
        raise ValueError("theme_journal_scope_invalid")
    attempts = journal.get("attempts")
    if not isinstance(attempts, dict) or not set(attempts) <= dict(CONFIGURED_THEMES).keys():
        raise ValueError("theme_journal_attempt_invalid")
    for attempt in attempts.values():
        if (not isinstance(attempt, dict) or attempt.get("status") not in {"started", "ingested", "observed", "failed", "unknown"}
                or not isinstance(attempt.get("window_date"), str)):
            raise ValueError("theme_journal_attempt_invalid")
        try:
            parsed = datetime.strptime(attempt["window_date"], "%Y-%m-%d").date()
            started = datetime.fromisoformat(attempt["started_at"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("theme_journal_attempt_invalid") from None
        if parsed.isoformat() != attempt["window_date"] or started.utcoffset() is None:
            raise ValueError("theme_journal_attempt_invalid")
    if journal.get("status") not in {"initialized", "running", "completed", "partial", "failed", "skipped_before_window"}:
        raise ValueError("theme_journal_status_invalid")
    return journal


def initialize_journal(state):
    if any(path.exists() or path.is_symlink() for path in (state / STATE_NAME, state / MARKER_NAME)):
        raise ValueError("theme_journal_already_exists_restore_original_records")
    base = {"schema_version": 1, "config_version": CONFIG_VERSION,
            "project": project_name(state), "scheduler_id": uuid.uuid4().hex}
    ops.private_write(state / MARKER_NAME, json.dumps(base) + "\n")
    journal = {**base, "mode": "paper", "status": "initialized", "window_date": None,
               "current_window_start": None, "started_at": None, "completed_at": None,
               "boards": [], "attempts": {}, "approvals_created": 0}
    atomic_state(state / STATE_NAME, journal)


def validated_result(result, board, name, day, now):
    if len(result.stdout.encode()) > MAX_OUTPUT_BYTES:
        raise ValueError("theme_job_response_invalid")
    payload = json.loads(result.stdout)
    if (not isinstance(payload, dict) or payload.get("board_code") != board or payload.get("board_name") != name
            or payload.get("window_date") != day or type(payload.get("attempted_network")) is not bool):
        raise ValueError("theme_job_response_invalid")
    status = payload.get("status")
    allowed = {"ingested", "skip_already_observed", "skip_attempted", "failed", "skipped_before_window"}
    if status not in allowed or (result.returncode == 0) != (status in {"ingested", "skip_already_observed"}):
        raise ValueError("theme_job_response_invalid")
    safe = {"board_code": board, "board_name": name, "status": status,
            "attempted_network": payload["attempted_network"]}
    if status in {"ingested", "skip_already_observed"}:
        received = datetime.fromisoformat(payload["received_at"])
        _, start = window(now)
        if (received.utcoffset() is None or not start <= received <= now
                or received.astimezone(SHANGHAI).date().isoformat() != day
                or payload.get("evidence_status") != "verified"
                or not isinstance(payload.get("receipt_id"), str)
                or not re.fullmatch(r"[a-f0-9]{32}", payload["receipt_id"])
                or not isinstance(payload.get("source_hash"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", payload["source_hash"])
                or type(payload.get("member_count")) is not int or not 0 <= payload["member_count"] <= 10000):
            raise ValueError("theme_job_receipt_invalid")
        safe.update({key: payload[key] for key in ("receipt_id", "received_at", "source_hash", "member_count", "evidence_status")})
    else:
        errors = {"theme_receipt_verification_failed", "theme_ingest_failed", "theme_post_ingest_verification_failed",
                  "window_attempt_already_recorded", "window_not_open"}
        if payload.get("error") not in errors:
            raise ValueError("theme_job_response_invalid")
        safe["error"] = payload["error"]
    return safe



def cleanup_container(container):
    try:
        removed = subprocess.run(["docker", "rm", "-f", container], capture_output=True,
                                 text=True, timeout=8, check=False)
        if removed.returncode == 0:
            return True
        inspected = subprocess.run(["docker", "container", "inspect", container], capture_output=True,
                                   text=True, timeout=2, check=False)
        absent = {f"Error: No such object: {container}",
                  f"Error response from daemon: No such container: {container}",
                  f"Error: No such container: {container}"}
        return (inspected.returncode == 1 and inspected.stderr.strip() in absent
                and inspected.stdout.strip() in {"", "[]"})
    except (OSError, subprocess.SubprocessError):
        return False


def run_board(state, board, name, allow_ingest, day, deadline):
    container = "quantdinger-theme-" + uuid.uuid4().hex
    command = ops.compose(state, "run", "--rm", "--no-deps", "-T", "--name", container,
                          "worker", "python", "-c", CONTAINER_SCRIPT)
    failure = {"board_code": board, "board_name": name, "status": "unknown", "error": "theme_job_unknown"}
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("theme_job_budget_exhausted")
        result = subprocess.run(command, input=json.dumps({"board_code": board, "board_name": name,
                                "allow_ingest": allow_ingest}), capture_output=True, text=True,
                                timeout=min(JOB_TIMEOUT, remaining), check=False)
        outcome = validated_result(result, board, name, day, utc_now())
    except subprocess.TimeoutExpired:
        outcome = {**failure, "error": "theme_job_timeout"}
    except (OSError, ValueError, TypeError, KeyError):
        outcome = failure
    finally:
        if not cleanup_container(container):
            outcome = {**failure, "error": "theme_job_cleanup_unknown"}
    return outcome


def sync(state):
    with ops.operation_lock(state):
        journal = load_journal(state)
        now = utc_now()
        day, start = window(now)
        if now < start:
            print(json.dumps({"status": "skipped_before_window", "window_date": day,
                              "config_version": CONFIG_VERSION, "approvals_created": 0}))
            return 0
        if any(attempt["window_date"] > day for attempt in journal["attempts"].values()):
            raise ValueError("theme_journal_future_window")
        deadline = time.monotonic() + TOTAL_TIMEOUT
        journal.update(status="running", window_date=day, current_window_start=start.isoformat(),
                       started_at=now.isoformat(), completed_at=None, boards=[])
        atomic_state(state / STATE_NAME, journal)
        cleanup_uncertain = False
        for board, name in CONFIGURED_THEMES:
            current = utc_now()
            if cleanup_uncertain or window(current)[0] != day or current < start or time.monotonic() >= deadline:
                journal["boards"].append({"board_code": board, "board_name": name, "status": "unknown",
                                          "error": "theme_job_budget_or_window_exhausted"})
                continue
            prior = journal["attempts"].get(board)
            allow_ingest = not prior or prior["window_date"] != day
            if allow_ingest:
                journal["attempts"][board] = {"window_date": day, "status": "started", "started_at": current.isoformat()}
                atomic_state(state / STATE_NAME, journal)
            outcome = run_board(state, board, name, allow_ingest, day, deadline)
            journal["boards"].append(outcome)
            cleanup_uncertain = outcome.get("error") == "theme_job_cleanup_unknown"
            attempt = journal["attempts"][board]
            if outcome["status"] != "skip_attempted":
                attempt.update(status="observed" if outcome["status"] == "skip_already_observed" else
                               outcome["status"] if outcome["status"] in {"ingested", "failed"} else "unknown",
                               completed_at=utc_now().isoformat())
                if "error" in outcome:
                    attempt["error"] = outcome["error"]
                else:
                    attempt.pop("error", None)
            atomic_state(state / STATE_NAME, journal)
        successes = sum(row["status"] in {"ingested", "skip_already_observed"} for row in journal["boards"])
        journal.update(status="completed" if successes == len(CONFIGURED_THEMES) else "partial" if successes else "failed",
                       completed_at=utc_now().isoformat())
        atomic_state(state / STATE_NAME, journal)
        summary = {key: journal[key] for key in ("config_version", "status", "window_date", "completed_at", "boards", "approvals_created")}
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if journal["status"] == "completed" else 1


def install_timer(state):
    with ops.operation_lock(state):
        project = project_name(state)
        unit = "quantdinger-themes-" + project
        directory = Path.home() / ".config/systemd/user"
        directory.mkdir(parents=True, exist_ok=True)
        paths = [directory / (unit + suffix) for suffix in (".service", ".timer")]
        if any(path.exists() for path in paths):
            raise ValueError("theme_timer_already_exists")
        command = " ".join(ops.systemd_argument(value) for value in
                           [sys.executable, Path(__file__).resolve(), "--state-dir", state, "run"])
        service = f"""[Unit]
Description=Current configured theme observations, never approvals ({project})
ConditionPathExists={state / 'config.env'}

[Service]
Type=oneshot
UMask=0077
TimeoutStartSec=270s
TimeoutStopSec=30s
ExecStart={command}
"""
        timer = f"""[Unit]
Description=Configured themes at 20:10 Asia/Shanghai ({project})

[Timer]
OnCalendar=*-*-* 20:10:00 Asia/Shanghai
RandomizedDelaySec=60
Persistent=true
Unit={unit}.service

[Install]
WantedBy=timers.target
"""
        initialize_journal(state)
        ops.private_write(paths[0], service)
        ops.private_write(paths[1], timer)
        ops.run(["systemctl", "--user", "daemon-reload"])
        ops.run(["systemctl", "--user", "enable", "--now", unit + ".timer"])
        print(json.dumps({"status": "timer_installed", "config_version": CONFIG_VERSION, "approvals_created": 0}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("command", choices=("run", "install-timer"))
    args = parser.parse_args(argv)
    state = args.state_dir.expanduser().resolve()
    if state == ops.ROOT or ops.ROOT in state.parents:
        parser.error("Keep deployment journals outside the checkout")
    if args.command == "install-timer":
        install_timer(state)
        return 0
    return sync(state)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, ops.stop_on_signal)
    signal.signal(signal.SIGINT, ops.stop_on_signal)
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
        print(json.dumps({"status": "failed", "error": "theme_scheduler_failed_closed", "approvals_created": 0}))
        raise SystemExit(1) from None
