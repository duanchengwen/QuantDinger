"""Bind a local PDF excerpt artifact to already verified announcement evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

SHA256 = re.compile(r"[a-f0-9]{64}\Z")
RECEIPT_ID = re.compile(r"[a-f0-9]{32}\Z")
ARTICLE_ID = re.compile(r"[0-9]{1,20}\Z")
MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_EXCERPT_CHARS = 12_000
PREVIEW_EXCERPT_CHARS = 2_000


class ExcerptEvidenceError(ValueError):
    """An excerpt artifact cannot be trusted as supplied."""


def _time(value):
    value = datetime.fromisoformat(value)
    if value.utcoffset() is None:
        raise ValueError()
    return value.astimezone(UTC)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def load_excerpt_receipt(path, expected_sha256):
    """Require a separate expected hash and verify all supplied text before use."""
    try:
        if not SHA256.fullmatch(expected_sha256) or not Path(path).is_file():
            raise ValueError()
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(raw) > MAX_ARTIFACT_BYTES or _digest(raw) != expected_sha256:
            raise ValueError()
        artifact = json.loads(raw)
        received = _time(artifact["archive_received_at"])
        derived = _time(artifact["derived_at"])
        if (
            artifact["schema_version"] != 1
            or artifact["source"] != "cninfo"
            or artifact["content_kind"] != "announcement_pdf_excerpt"
            or artifact["auto_approval_eligible"] is not False
            or artifact["parser"] != "pdftotext"
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", artifact["parser_version"])
            or artifact["page_range_requested"] != [1, 3]
            or not RECEIPT_ID.fullmatch(artifact["receipt_id"])
            or not SHA256.fullmatch(artifact["receipt_manifest_sha256"])
            or not SHA256.fullmatch(artifact["evidence_backup_sha256"])
            or derived < received
            or not isinstance(artifact["items"], list)
            or len(artifact["items"]) > 15
        ):
            raise ValueError()
        items = {}
        seen = set()
        for item in artifact["items"]:
            identity = item["article_id"]
            if not ARTICLE_ID.fullmatch(identity) or identity in seen:
                raise ValueError()
            seen.add(identity)
            if item["status"] in (
                "timeout",
                "extraction_failed",
                "output_limit",
                "invalid_text_encoding",
                "text_unavailable",
            ):
                continue
            excerpt = item["excerpt"]
            if (
                item["status"] != "extracted"
                or not isinstance(excerpt, str)
                or not 1 <= len(excerpt) <= MAX_EXCERPT_CHARS
                or not excerpt.strip()
                or _digest(excerpt.encode("utf-8")) != item["excerpt_sha256"]
                or not all(
                    SHA256.fullmatch(item[key])
                    for key in ("source_pdf_sha256", "source_index_sha256", "extracted_text_sha256")
                )
                or type(item["text_truncated"]) is not bool
                or not isinstance(item["source_url"], str)
                or item["timestamp_status_at_archive"] not in ("valid", "future", "missing")
                or (item["published_at"] is not None and not isinstance(item["published_at"], str))
            ):
                raise ValueError()
            items[identity] = item
    except (KeyError, TypeError, ValueError, OSError, RecursionError):
        raise ExcerptEvidenceError("invalid_news_excerpt_evidence") from None
    return {
        **artifact,
        "items": items,
        "artifact_sha256": expected_sha256,
        "archive_received_at": received,
        "derived_at": derived,
    }


def match_excerpt(receipt, article, observed_receipt, cutoff):
    """Use only the exact archived revision and evidence available at the cutoff."""
    if receipt is None or receipt["receipt_id"] != article.get("receipt_id") or receipt["derived_at"] > cutoff:
        return None
    item = receipt["items"].get(article["article_id"])
    if item is None:
        return None
    if (
        observed_receipt is None
        or receipt["receipt_manifest_sha256"] != observed_receipt["source_hash"]
        or receipt["archive_received_at"] != _time(observed_receipt["received_at"])
        or receipt["archive_received_at"] != _time(article["known_at"])
        or item["source_url"] != article["source_url"]
        or item["source_pdf_sha256"] != article["pdf_hash"]
        or item["source_index_sha256"] != article["source_hash"]
        or item["published_at"] != article["published_at"]
        or item["timestamp_status_at_archive"] != article["timestamp_status"]
    ):
        raise ExcerptEvidenceError("news_excerpt_source_mismatch")
    excerpt = item["excerpt"][:PREVIEW_EXCERPT_CHARS]
    return {
        "content_kind": "announcement_pdf_excerpt",
        "content": excerpt,
        "page_range_requested": receipt["page_range_requested"],
        "derived_at": receipt["derived_at"].isoformat(),
        "artifact_sha256": receipt["artifact_sha256"],
        "receipt_manifest_sha256": receipt["receipt_manifest_sha256"],
        "stored_excerpt_sha256": item["excerpt_sha256"],
        "provided_excerpt_sha256": _digest(excerpt.encode("utf-8")),
        "truncated": item["text_truncated"] or len(excerpt) < len(item["excerpt"]),
    }
