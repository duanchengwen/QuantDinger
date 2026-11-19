"""Store and re-verify immutable backtests without granting execution."""
import hashlib
import json
import re
import uuid

from psycopg2.extras import Json


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _valid(report):
    return (isinstance(report, dict) and report.get("status") == "candidate"
            and report.get("execution_allowed") is False
            and isinstance(report.get("config"), dict)
            and isinstance(report.get("reason_code"), str)
            and isinstance(report.get("evidence"), dict)
            and isinstance(report.get("metrics"), dict))


def save_result(cursor, tenant_id, account_id, research_run_id, report):
    if not _valid(report):
        raise ValueError("strategy_backtest_unavailable")
    ref = {"run_id": uuid.uuid4().hex, "result_sha256": _digest(report)}
    cursor.execute("""INSERT INTO qd_ashare_strategy_backtests
        (tenant_id,account_id,id,research_run_id,result_sha256,report) VALUES(%s,%s,%s,%s,%s,%s)
        """, (tenant_id, account_id, ref["run_id"], research_run_id, ref["result_sha256"], Json(report)))
    return ref


def verify_reference(cursor, tenant_id, account_id, config):
    ref = config.get("backtest_reference")
    if ref is None:
        return None
    research = config.get("research_reference")
    if (not isinstance(ref, dict) or set(ref) != {"run_id", "result_sha256"}
            or not isinstance(ref["run_id"], str) or not re.fullmatch("[a-f0-9]{32}", ref["run_id"])
            or not isinstance(research, dict) or not isinstance(research.get("run_id"), str)):
        raise ValueError("strategy_backtest_unavailable")
    cursor.execute("""SELECT research_run_id,result_sha256,report FROM qd_ashare_strategy_backtests
        WHERE tenant_id=%s AND account_id=%s AND id=%s""", (tenant_id, account_id, ref["run_id"]))
    row = cursor.fetchone()
    if row is None:
        raise ValueError("strategy_backtest_unavailable")
    report = row["report"]
    terms = {key: value for key, value in config.items() if key not in {"backtest_reference", "research_reference"}}
    if (row["research_run_id"] != research["run_id"] or row["result_sha256"] != ref["result_sha256"]
            or not _valid(report) or row["result_sha256"] != _digest(report) or report["config"] != terms):
        raise ValueError("strategy_backtest_unavailable")
    return {**ref, "status": report["status"], "reason_code": report["reason_code"],
            "evidence": report["evidence"], "metrics": report["metrics"]}
