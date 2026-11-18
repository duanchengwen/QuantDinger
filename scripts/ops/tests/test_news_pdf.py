"""The optional extractor only reads verified receipt members and emits bounded excerpts."""

import gzip
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import news_pdf

RECEIPT = "1" * 32
PREFIX = f"./evidence/news/news-{RECEIPT}/"
INDEX = b'{"announcements":[]}'
PDF = b"%PDF-1.4\ntest archive bytes"
URL = "https://static.cninfo.com.cn/finalpage/2026-11-18/123.pdf"


def sha(raw):
    return sha256(raw).hexdigest()


class ExtractionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "backup"
        self.source.mkdir()
        self.output = self.root / "result.json"
        self.archive = self.source / "evidence.tar.gz"
        self.item = {
            "source_url": URL,
            "article_id": "123",
            "source_hash": sha(INDEX),
            "pdf_hash": sha(PDF),
            "published_at": None,
            "publication_time_precision": "provider_timestamp_unverified",
            "timestamp_status": "missing",
        }
        self.manifest = {
            "schema_version": 1,
            "parser_version": "cninfo-announcement-titles-v1",
            "receipt_id": RECEIPT,
            "source": "cninfo",
            "received_at": "2020-01-01T00:00:00+00:00",
            "normalized": {"items": [self.item]},
            "pages": [
                {"kind": "index", "file": "page-000.bin", "sha256": sha(INDEX)},
                {"kind": "pdf", "url": URL, "file": "page-001.bin", "sha256": sha(PDF)},
            ],
        }

    def write_backup(self, pdf=PDF, *, duplicate=False):
        raw = json.dumps(self.manifest).encode()
        with tarfile.open(self.archive, "w:gz") as archive:
            members = [
                ("manifest.json", raw),
                ("page-000.bin", INDEX),
                ("page-001.bin", pdf),
            ]
            if duplicate:
                members.append(("page-001.bin", pdf))
            for name, data in members:
                info = tarfile.TarInfo(PREFIX + name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        files = news_pdf.ops.LEGACY_BACKUP_FILES
        for name in files - {"evidence.tar.gz"}:
            (self.source / name).write_bytes(b"sample")
        hashes = {name: sha((self.source / name).read_bytes()) for name in files}
        (self.source / "manifest.json").write_text(json.dumps({"files": hashes}))
        return raw

    def cli(self, output=None):
        console = io.StringIO()
        with redirect_stdout(console):
            code = news_pdf.main(
                [
                    "--backup",
                    str(self.source),
                    "--receipt",
                    RECEIPT,
                    "--output",
                    str(output or self.output),
                ]
            )
        return code, json.loads(console.getvalue())

    def test_writes_private_evidence_separately_and_preserves_origins(self):
        manifest_bytes = self.write_backup()
        text = "公告内容"
        with (
            patch.object(news_pdf, "_sandbox_version", return_value="22.02.0"),
            patch.object(
                news_pdf,
                "_extract",
                return_value={"status": "extracted", "excerpt": text},
            ) as extract,
        ):
            self.assertEqual(
                self.cli(), (0, {"receipt_id": RECEIPT, "extracted": 1, "pdfs": 1})
            )
            extract.assert_called_once_with(PDF)
            self.assertEqual(self.cli(), (1, {"error": "news_output_exists"}))
        result = json.loads(self.output.read_text())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["receipt_manifest_sha256"], sha(manifest_bytes))
        self.assertGreater(result["derived_at"], result["archive_received_at"])
        self.assertFalse(result["auto_approval_eligible"])
        self.assertEqual(result["page_range_requested"], [1, 3])
        self.assertEqual(result["items"][0]["source_pdf_sha256"], sha(PDF))
        self.assertEqual(result["items"][0]["source_index_sha256"], sha(INDEX))
        self.assertIsNone(result["items"][0]["published_at"])

    def test_latest_uses_existing_ingestion_receipt_only_once(self):
        manifest = self.write_backup()
        status = {
            "jobs": {
                "news": {
                    "status": "ingested",
                    "source_hash": sha(manifest),
                    "receipt_id": RECEIPT,
                    "item_count": 1,
                }
            }
        }
        expected_archive = gzip.decompress(self.archive.read_bytes())

        def export(state, receipt, target):
            self.assertEqual(receipt, RECEIPT)
            target.write_bytes(expected_archive)

        with (
            patch.object(news_pdf, "load_job_status", return_value=status),
            patch.object(news_pdf, "_export_receipt", side_effect=export) as read,
            patch.object(news_pdf, "_sandbox_version", return_value="22.02.0"),
            patch.object(news_pdf, "_extract", return_value={"status": "extracted"}),
        ):
            result = news_pdf.extract_latest(self.source, self.root)
            again = news_pdf.extract_latest(self.source, self.root)
        self.assertEqual(result["status"], "extracted")
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(again["status"], "already_extracted")
        read.assert_called_once()
        raw = (self.root / f"{RECEIPT}.json").read_bytes()
        artifact = json.loads(raw)
        self.assertEqual(result["artifact_sha256"], sha(raw))
        self.assertEqual(artifact["evidence_archive_sha256"], sha(expected_archive))
        self.assertEqual(artifact["receipt_manifest_sha256"], sha(manifest))

    def test_latest_rejects_archive_different_from_completed_receipt(self):
        self.write_backup()
        status = {
            "jobs": {
                "news": {
                    "status": "ingested",
                    "source_hash": "a" * 64,
                    "receipt_id": RECEIPT,
                    "item_count": 1,
                }
            }
        }

        def export(state, receipt, target):
            target.write_bytes(gzip.decompress(self.archive.read_bytes()))

        with (
            patch.object(news_pdf, "load_job_status", return_value=status),
            patch.object(news_pdf, "_export_receipt", side_effect=export),
            patch.object(news_pdf, "_sandbox_version") as start,
            self.assertRaisesRegex(
                news_pdf.EvidenceError, "news_manifest_hash_mismatch"
            ),
        ):
            news_pdf.extract_latest(self.source, self.root)
        start.assert_not_called()
        self.assertFalse((self.root / f"{RECEIPT}.json").exists())

    def test_latest_does_not_read_archive_during_ingestion_or_after_failure(self):
        for status in ("started", "failed", "unknown"):
            with (
                self.subTest(status=status),
                patch.object(
                    news_pdf,
                    "load_job_status",
                    return_value={"jobs": {"news": {"status": status}}},
                ),
                patch.object(news_pdf, "_export_receipt") as export,
            ):
                result = news_pdf.extract_latest(self.source, self.root)
                self.assertEqual(result["status"], "waiting_for_ingestion")
                export.assert_not_called()

    def test_templates_trigger_only_after_ingestion_success(self):
        (self.source / "deployment.json").write_text(json.dumps({"project": "qd-test"}))
        unit_dir = self.root / "units"
        service, dropin = news_pdf.write_units(self.source, self.root, unit_dir)
        self.assertEqual(service.name, "quantdinger-research-news-pdf-qd-test.service")
        self.assertEqual(
            dropin.parent.name, "quantdinger-research-news-qd-test.service.d"
        )
        self.assertIn("TimeoutStartSec=120\n", service.read_text())
        self.assertIn('"--state-dir"', service.read_text())
        self.assertNotIn("install", service.read_text())
        self.assertEqual(dropin.read_text(), f"[Unit]\nOnSuccess={service.name}\n")
        with self.assertRaisesRegex(
            news_pdf.EvidenceError, "news_pdf_unit_already_exists"
        ):
            news_pdf.write_units(self.source, self.root, unit_dir)

    def test_oversized_sidecar_creates_no_private_output(self):
        oversized = {"excerpt": "字" * (news_pdf.MAX_OUTPUT_BYTES // 3 + 1)}
        with self.assertRaisesRegex(
            news_pdf.EvidenceError, "news_excerpt_artifact_size_exceeded"
        ):
            news_pdf._write_excerpt(self.output, oversized)
        self.assertFalse(self.output.exists())

    def test_backup_digest_mismatch_stops_before_the_parser(self):
        self.write_backup()
        with self.archive.open("ab") as stream:
            stream.write(b"tampered")
        with patch.object(news_pdf, "_sandbox_version") as start:
            self.assertEqual(
                self.cli(), (1, {"error": "news_backup_or_output_unavailable"})
            )
            start.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_pdf_hash_mismatch_is_rejected_even_with_a_valid_backup_checksum(self):
        self.write_backup(PDF + b"tampered")
        with patch.object(news_pdf, "_sandbox_version") as start:
            self.assertEqual(self.cli(), (1, {"error": "news_original_hash_mismatch"}))
            start.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_duplicate_archive_members_are_rejected(self):
        self.write_backup(duplicate=True)
        self.assertEqual(self.cli(), (1, {"error": "news_archive_member_invalid"}))

    def test_pdf_item_requires_a_verified_index_hash(self):
        self.item["source_hash"] = "0" * 64
        self.write_backup()
        self.assertEqual(self.cli(), (1, {"error": "news_original_hash_mismatch"}))

    def test_future_receipt_is_rejected_without_opening_the_pdf(self):
        self.manifest["received_at"] = "2999-01-01T00:00:00+00:00"
        self.write_backup()
        with patch.object(news_pdf, "_sandbox_version") as start:
            self.assertEqual(self.cli(), (1, {"error": "news_manifest_time_invalid"}))
            start.assert_not_called()

    def test_output_within_backup_is_rejected(self):
        self.assertEqual(
            self.cli(self.source / "result.json"),
            (1, {"error": "news_output_requires_separate_private_path"}),
        )

    def test_unavailable_sandbox_yields_no_output(self):
        self.write_backup()
        with patch.object(news_pdf.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(self.cli(), (1, {"error": "news_pdf_sandbox_unavailable"}))
        self.assertFalse(self.output.exists())

    def test_pdf_timeout_has_no_excerpt(self):
        with patch.object(
            news_pdf.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("pdftotext", 6),
        ):
            self.assertEqual(news_pdf._extract(PDF), {"status": "timeout"})

    def test_empty_text_is_marked_unavailable(self):
        self.assertEqual(self.extract_bytes(b"\r\n\f"), {"status": "text_unavailable"})

    def test_invalid_utf8_does_not_leak_partial_text(self):
        self.assertEqual(
            self.extract_bytes(b"content\xff"), {"status": "invalid_text_encoding"}
        )

    def test_oversized_parser_output_is_rejected(self):
        self.assertEqual(
            self.extract_bytes(b"a" * (news_pdf.MAX_OUTPUT_BYTES + 1)),
            {"status": "output_limit"},
        )

    def test_large_text_is_bounded_and_hashes_distinguish_the_prefix(self):
        raw = ("文" * (news_pdf.MAX_EXCERPT_CHARS + 1)).encode()
        item = self.extract_bytes(raw)
        self.assertTrue(item["text_truncated"])
        self.assertEqual(len(item["excerpt"]), news_pdf.MAX_EXCERPT_CHARS)
        self.assertEqual(item["extracted_text_sha256"], sha(raw))
        self.assertEqual(item["excerpt_sha256"], sha(item["excerpt"].encode()))

    def extract_bytes(self, raw):
        def subprocess_run(cmd, *, stdout, **kwargs):
            self.assertIn("--unshare-all", cmd)
            self.assertIn("--clearenv", cmd)
            self.assertEqual(
                cmd[-9:],
                ["/usr/bin/pdftotext", "-f", "1", "-l", "3", "-enc", "UTF-8", "-", "-"],
            )
            stdout.write(raw)
            return subprocess.CompletedProcess(cmd, 0)

        with patch.object(news_pdf.subprocess, "run", side_effect=subprocess_run):
            return news_pdf._extract(PDF)
