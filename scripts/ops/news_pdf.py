#!/usr/bin/env python3
"""Derive bounded announcement excerpts from a verified paper receipt."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import paper_stack as ops
from research_jobs import load_job_status

RECEIPT_ID = re.compile(r"[a-f0-9]{32}\Z")
ARTICLE_ID = re.compile(r"[0-9]{1,20}\Z")
PDF_URL = re.compile(
    r"https://static\.cninfo\.com\.cn/finalpage/[0-9]{4}-[0-9]{2}-[0-9]{2}/([0-9]{1,20})\.[pP][dD][fF]\Z"
)
MAX_MANIFEST_BYTES = 256 * 1024
MAX_INDEX_BYTES = 256 * 1024
MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_PDFS = 15
MAX_RECEIPT_ARCHIVE_BYTES = 18 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_EXCERPT_CHARS = 12_000
PAGE_LIMIT = 3
PDF_TIMEOUT = 6

SANDBOX = (
    "/usr/bin/bwrap",
    "--unshare-all",
    "--die-with-parent",
    "--new-session",
    "--clearenv",
    "--setenv",
    "LANG",
    "C.UTF-8",
    "--ro-bind",
    "/usr",
    "/usr",
    "--symlink",
    "usr/bin",
    "/bin",
    "--symlink",
    "usr/lib",
    "/lib",
    "--symlink",
    "usr/lib64",
    "/lib64",
    "--dev",
    "/dev",
    "--tmpfs",
    "/tmp",
    "--chdir",
    "/",
    "/usr/bin/prlimit",
    "--as=268435456",
    "--cpu=3",
    f"--fsize={MAX_OUTPUT_BYTES}",
    "--",
    "/usr/bin/pdftotext",
)


class EvidenceError(ValueError):
    """A stable extraction or provenance failure."""


def _digest(data):
    return sha256(data).hexdigest()


def _member(archive, path, limit):
    matches = [item for item in archive.getmembers() if item.name == path]
    if len(matches) != 1 or not matches[0].isfile() or not 0 < matches[0].size <= limit:
        raise EvidenceError("news_archive_member_invalid")
    with archive.extractfile(matches[0]) as file:
        return file.read(limit + 1)


def _verified_inputs(archive, receipt_id, expected_manifest_sha256=None):
    prefix = f"./evidence/news/news-{receipt_id}/"
    raw = _member(archive, prefix + "manifest.json", MAX_MANIFEST_BYTES)
    manifest_sha = _digest(raw)
    if (
        expected_manifest_sha256 is not None
        and manifest_sha != expected_manifest_sha256
    ):
        raise EvidenceError("news_manifest_hash_mismatch")
    manifest = json.loads(raw)
    if (
        manifest["receipt_id"] != receipt_id
        or manifest["source"] != "cninfo"
        or manifest["schema_version"] != 1
        or manifest["parser_version"] != "cninfo-announcement-titles-v1"
    ):
        raise EvidenceError("news_manifest_scope_mismatch")
    received = datetime.fromisoformat(manifest["received_at"])
    if received.utcoffset() is None:
        raise EvidenceError("news_manifest_time_invalid")
    items = manifest["normalized"]["items"]
    pages = manifest["pages"]
    if (
        not isinstance(items, list)
        or len(items) > MAX_PDFS
        or not isinstance(pages, list)
        or len(pages) > MAX_PDFS + 3
    ):
        raise EvidenceError("news_archive_limits_exceeded")
    pdfs, indexes = {}, set()
    for index, page in enumerate(pages):
        kind = page["kind"]
        if page["file"] != f"page-{index:03d}.bin" or kind not in ("index", "pdf"):
            raise EvidenceError("news_archive_member_invalid")
        blob = _member(
            archive,
            prefix + page["file"],
            MAX_PDF_BYTES if kind == "pdf" else MAX_INDEX_BYTES,
        )
        if _digest(blob) != page["sha256"]:
            raise EvidenceError("news_original_hash_mismatch")
        if kind == "index":
            indexes.add(page["sha256"])
            continue
        if page["url"] in pdfs or not blob.startswith(b"%PDF-"):
            raise EvidenceError("news_pdf_scope_invalid")
        pdfs[page["url"]] = (page["sha256"], blob)
    if (
        not indexes
        or set(pdfs) != {item["source_url"] for item in items}
        or len(items) != len(pdfs)
    ):
        raise EvidenceError("news_pdf_receipt_incomplete")
    inputs = []
    for item in items:
        url = item["source_url"]
        article = item["article_id"]
        match = PDF_URL.fullmatch(url)
        pdf_hash, pdf = pdfs[url]
        if not ARTICLE_ID.fullmatch(article) or not match or match[1] != article:
            raise EvidenceError("news_pdf_scope_invalid")
        if pdf_hash != item["pdf_hash"] or item["source_hash"] not in indexes:
            raise EvidenceError("news_original_hash_mismatch")
        inputs.append((item, pdf))
    return manifest_sha, received, inputs


def _sandbox_version():
    try:
        proc = subprocess.run(
            [*SANDBOX, "-v"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=PDF_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise EvidenceError("news_pdf_sandbox_unavailable") from None
    match = re.search(
        rb"(?m)^pdftotext version ([0-9]+(?:\.[0-9]+){1,3})\r?$", proc.stderr
    )
    if proc.returncode or match is None:
        raise EvidenceError("news_pdf_sandbox_unavailable")
    return match[1].decode("ascii")


def _extract(pdf):
    with tempfile.TemporaryFile() as output:
        try:
            proc = subprocess.run(
                [*SANDBOX, "-f", "1", "-l", str(PAGE_LIMIT), "-enc", "UTF-8", "-", "-"],
                input=pdf,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=PDF_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"status": "timeout"}
        if proc.returncode:
            return {"status": "extraction_failed"}
        output.seek(0)
        raw = output.read(MAX_OUTPUT_BYTES + 1)
    if len(raw) > MAX_OUTPUT_BYTES:
        return {"status": "output_limit"}
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return {"status": "invalid_text_encoding"}
    if not any(character.isalnum() for character in text):
        return {"status": "text_unavailable"}
    excerpt = text[:MAX_EXCERPT_CHARS]
    return {
        "status": "extracted",
        "excerpt": excerpt,
        "text_truncated": len(excerpt) != len(text),
        "excerpt_sha256": _digest(excerpt.encode("utf-8")),
        "extracted_text_sha256": _digest(raw),
    }


def _derive(receipt_id, manifest_sha, received, inputs, provenance):
    if received > datetime.now(UTC):
        raise EvidenceError("news_manifest_time_invalid")
    version = _sandbox_version()
    results = []
    for item, pdf in inputs:
        results.append(
            {
                "article_id": item["article_id"],
                "source_url": item["source_url"],
                "source_index_sha256": item["source_hash"],
                "source_pdf_sha256": item["pdf_hash"],
                "published_at": item["published_at"],
                "publication_time_precision": item["publication_time_precision"],
                "timestamp_status_at_archive": item["timestamp_status"],
                **_extract(pdf),
            }
        )
    derived_at = datetime.now(UTC)
    if derived_at < received:
        raise EvidenceError("news_manifest_time_invalid")
    return {
        "schema_version": 1,
        "source": "cninfo",
        "receipt_id": receipt_id,
        "receipt_manifest_sha256": manifest_sha,
        **provenance,
        "archive_received_at": received.isoformat(),
        "derived_at": derived_at.isoformat(),
        "content_kind": "announcement_pdf_excerpt",
        "page_range_requested": [1, PAGE_LIMIT],
        "parser": "pdftotext",
        "parser_version": version,
        "items": results,
        "auto_approval_eligible": False,
    }


def extract_receipt(source, receipt_id):
    """Verify membership and original bytes before opening any file in the parser."""
    if not RECEIPT_ID.fullmatch(receipt_id):
        raise EvidenceError("invalid_news_receipt_id")
    backup = ops.verify_backup(source)
    with tarfile.open(source / "evidence.tar.gz", "r:gz") as archive:
        inputs = _verified_inputs(archive, receipt_id)
    return _derive(
        receipt_id,
        *inputs,
        {"evidence_backup_sha256": backup["files"]["evidence.tar.gz"]},
    )


def _write_excerpt(output, result):
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    payload = body.encode("utf-8")
    if len(payload) > MAX_OUTPUT_BYTES:
        raise EvidenceError("news_excerpt_artifact_size_exceeded")
    with tempfile.TemporaryDirectory(
        prefix=".paper-news-", dir=output.parent
    ) as private:
        stage = Path(private) / "excerpt.json"
        ops.private_write(stage, body)
        os.link(stage, output)
    return _digest(payload)


def _export_receipt(state, receipt_id, target):
    command = ops.compose(
        state,
        "exec",
        "-T",
        "api",
        "tar",
        "-C",
        "/app/data",
        "-cf",
        "-",
        f"./evidence/news/news-{receipt_id}",
    )
    with target.open("xb") as stream:
        try:
            result = subprocess.run(
                [
                    "/usr/bin/prlimit",
                    f"--fsize={MAX_RECEIPT_ARCHIVE_BYTES}",
                    "--",
                    *command,
                ],
                stdout=stream,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise EvidenceError("news_receipt_export_failed") from None
    if result.returncode or not 0 < target.stat().st_size <= MAX_RECEIPT_ARCHIVE_BYTES:
        raise EvidenceError("news_receipt_export_failed")


def extract_latest(state, output_dir):
    """Use the latest successful ingestion result without contacting its provider."""
    job = load_job_status(state)["jobs"].get("news", {})
    if job.get("status") != "ingested":
        return {"status": "waiting_for_ingestion", "execution_allowed": False}
    receipt_id = job["receipt_id"]
    manifest_sha = job["source_hash"]
    output = output_dir / f"{receipt_id}.json"
    if output.exists() or output.is_symlink():
        if (
            output.is_symlink()
            or not output.is_file()
            or output.stat().st_size > MAX_OUTPUT_BYTES
        ):
            raise EvidenceError("existing_news_excerpt_invalid")
        current = json.loads(output.read_bytes())
        if (
            not isinstance(current, dict)
            or current.get("receipt_id") != receipt_id
            or current.get("receipt_manifest_sha256") != manifest_sha
            or current.get("auto_approval_eligible") is not False
            or current.get("content_kind") != "announcement_pdf_excerpt"
            or not isinstance(current.get("items"), list)
            or len(current["items"]) != job["item_count"]
        ):
            raise EvidenceError("existing_news_excerpt_invalid")
        return {
            "status": "already_extracted",
            "receipt_id": receipt_id,
            "execution_allowed": False,
        }
    with tempfile.TemporaryDirectory(
        prefix=".paper-news-source-", dir=output_dir
    ) as private:
        target = Path(private) / "receipt.tar"
        _export_receipt(state, receipt_id, target)
        with tarfile.open(target, "r:") as archive:
            inputs = _verified_inputs(archive, receipt_id, manifest_sha)
        if len(inputs[2]) != job["item_count"]:
            raise EvidenceError("news_pdf_receipt_incomplete")
        result = _derive(
            receipt_id, *inputs, {"evidence_archive_sha256": ops.sha256(target)}
        )
        result_hash = _write_excerpt(output, result)
    return {
        "status": "extracted",
        "receipt_id": receipt_id,
        "artifact_sha256": result_hash,
        "pdfs": len(result["items"]),
        "extracted": sum(item["status"] == "extracted" for item in result["items"]),
        "execution_allowed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source_args = parser.add_mutually_exclusive_group(required=True)
    source_args.add_argument("--backup", type=Path)
    source_args.add_argument("--state-dir", type=Path)
    parser.add_argument("--receipt")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--unit-dir", type=Path, help="render inactive user-systemd service files"
    )
    args = parser.parse_args(argv)
    try:
        if args.state_dir is not None:
            if not args.output_dir or args.receipt or args.output:
                parser.error("--state-dir requires --output-dir")
            state = args.state_dir.expanduser().resolve()
            out = args.output_dir.expanduser().resolve()
            if out.is_relative_to(ops.ROOT) or out.is_relative_to(state):
                raise EvidenceError("news_output_requires_separate_private_path")
            if args.unit_dir:
                units = write_units(state, out, args.unit_dir.expanduser().resolve())
                print(
                    json.dumps(
                        {
                            "status": "templates_rendered",
                            "units": [str(unit) for unit in units],
                        }
                    )
                )
                return 0
            with ops.operation_lock(out):
                print(json.dumps(extract_latest(state, out)))
            return 0
        if not (args.receipt and args.output) or args.output_dir or args.unit_dir:
            parser.error("--backup requires --receipt and --output")
        source = args.backup.expanduser().resolve()
        output = args.output.expanduser()
        output = output.parent.resolve() / output.name
        if output.is_relative_to(ops.ROOT) or output.is_relative_to(source):
            raise EvidenceError("news_output_requires_separate_private_path")
        if output.exists() or output.is_symlink():
            raise EvidenceError("news_output_exists")
        result = extract_receipt(source, args.receipt)
        _write_excerpt(output, result)
        extracted = sum(item["status"] == "extracted" for item in result["items"])
        print(
            json.dumps(
                {
                    "receipt_id": args.receipt,
                    "pdfs": len(result["items"]),
                    "extracted": extracted,
                }
            )
        )
        return 0
    except EvidenceError as exc:
        print(json.dumps({"error": str(exc)}))
    except (KeyError, OSError, RuntimeError, ValueError, TypeError, tarfile.TarError):
        print(json.dumps({"error": "news_backup_or_output_unavailable"}))
    return 1


def write_units(state, output_dir, unit_dir):
    """Create reviewable coupled units without installing or starting a service."""
    project = json.loads((state / "deployment.json").read_bytes())["project"]
    ops.check_project(project)
    service = f"quantdinger-research-news-pdf-{project}.service"
    dropin = (
        unit_dir
        / f"quantdinger-research-news-{project}.service.d"
        / "10-derive-excerpts.conf"
    )
    unit = unit_dir / service
    for path in (unit, dropin):
        if path.exists() or path.is_symlink():
            raise EvidenceError("news_pdf_unit_already_exists")
    args = (
        sys.executable,
        Path(__file__).resolve(),
        "--state-dir",
        state,
        "--output-dir",
        output_dir,
    )
    command = " ".join(ops.systemd_argument(arg) for arg in args)
    dropin.parent.mkdir(parents=True, exist_ok=True)
    ops.private_write(
        unit,
        f"[Unit]\nDescription=Derive verified paper announcement excerpts ({project})\n\n"
        f"[Service]\nType=oneshot\nTimeoutStartSec=120\nExecStart={command}\n",
    )
    ops.private_write(dropin, f"[Unit]\nOnSuccess={service}\n")
    return unit, dropin


if __name__ == "__main__":
    raise SystemExit(main())
