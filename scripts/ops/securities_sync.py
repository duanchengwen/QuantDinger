#!/usr/bin/env python3
"""Bounded nightly reference-data job for an existing isolated paper deployment."""
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import paper_stack as ops


def sync(state, *, lookback=3, timeout=300):
    if type(lookback) is not int or not 1 <= lookback <= 20:
        raise ValueError("lookback must be 1..20")
    name = "quantdinger-securities-" + uuid.uuid4().hex[:16]
    command = ops.compose(state, "run", "--rm", "--no-deps", "-T", "--name", name,
                          "worker", "python", "-m", "app.ashare.securities_scheduler",
                          "--lookback", str(lookback), "--evidence-dir", "/app/data/evidence/securities")
    with ops.operation_lock(state):
        try:
            result = subprocess.run(command, timeout=timeout, check=False)
            return result.returncode
        except subprocess.TimeoutExpired:
            print(json.dumps({"status": "failed", "error": "securities_job_timeout", "approvals_created": 0}))
            return 1
        finally:
            # Kill only the unique job container, including after interruption.
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=20, check=False)


def install_timer(state):
    project = json.loads((state / "deployment.json").read_text())["project"]
    ops.check_project(project)
    unit = "quantdinger-securities-" + project
    directory = Path.home() / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / (unit + suffix) for suffix in (".service", ".timer")]
    if any(path.exists() for path in paths):
        raise ValueError("Securities timer already exists; refusing to overwrite")
    command = " ".join(ops.systemd_argument(x) for x in
                       [sys.executable, Path(__file__).resolve(), "--state-dir", state, "run"])
    service = f"""[Unit]
Description=Dated A-share reference data, no approvals ({project})
ConditionPathExists={state / 'config.env'}

[Service]
Type=oneshot
UMask=0077
TimeoutStartSec=6min
TimeoutStopSec=30s
ExecStart={command}
"""
    timer = f"""[Unit]
Description=Historical universe and watched status at 19:10 Asia/Shanghai ({project})

[Timer]
OnCalendar=*-*-* 19:10:00 Asia/Shanghai
RandomizedDelaySec=60
Persistent=true
Unit={unit}.service

[Install]
WantedBy=timers.target
"""
    ops.private_write(paths[0], service)
    ops.private_write(paths[1], timer)
    ops.run(["systemctl", "--user", "daemon-reload"])
    ops.run(["systemctl", "--user", "enable", "--now", unit + ".timer"])
    print(f"Installed {unit}.timer; nightly data is context, never approval.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("command", choices=("run", "install-timer"))
    parser.add_argument("--lookback", type=int, default=3)
    args = parser.parse_args()
    state = args.state_dir.expanduser().resolve()
    if state == ops.ROOT or ops.ROOT in state.parents:
        parser.error("Keep deployment secrets outside the checkout")
    if args.command == "install-timer":
        install_timer(state)
        return 0
    return sync(state, lookback=args.lookback)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, ops.stop_on_signal)
    signal.signal(signal.SIGINT, ops.stop_on_signal)
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
