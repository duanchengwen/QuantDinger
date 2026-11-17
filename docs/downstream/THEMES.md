# Current theme membership observations and provenance

[简体中文](THEMES_CN.md) · [User guide](USER_GUIDE.md) · [Backlog](backlog.json)

## Scope

Theme data uses Eastmoney public concept-directory and membership endpoints, the same source as AKShare concept-board adapters. These are provider-reported current members, not exchange-certified classifications or a historical membership database. Public accessibility does not establish long-term availability, licensing or an SLA.

Each successful observation stores the board code, exact name, every original response page, request parameters, per-page receipt times, manifest hash and supported securities. Both directory and membership pagination must complete with stable totals and unique identities. Missing pages, count mismatches, name conflicts and invalid fields prevent publication of a complete observation. Multiple HTTP pages are not an atomic provider snapshot: passing these checks cannot rule out same-count membership replacements during collection.

Archived bytes are the HTTP client's decoded response body, not TLS or compressed transport frames. The manifest binds page hashes, requests and receipt times. Queries revalidate the manifest, original pages and all database members before returning a page. Observations are append-only; current members never overwrite old versions.

## Time and research semantics

`published_at` is unknown. `known_at` uses the receipt time after complete collection. A historical knowledge query can only return already archived versions; times before first collection remain unobserved. There is no historical-date input for backfilling today's list and no caller-supplied past receipt timestamp.

Results separate provider total, supported Shanghai/Shenzhen A-share count and exclusions. Unsupported Beijing exchange or other securities are not silently treated as tradable. Membership establishes a provider classification, not current ST/suspension status, price bands, liquidity or trading authorization.

Broad preferences such as new energy may map to several concepts. This version requires an explicit board code or exact provider name (both must match when supplied together); it does not merge solar/new-energy vehicle concepts or alter saved user preferences. A read-only archive panel is available. Versioned preference mapping, periodic collection and research recommendations require separate integration.

## Operation and safety

Collection is a bounded independent task, separate from quote matching. Networking uses fixed HTTPS endpoints, no arbitrary URL, no redirects and no automatic retry. Request count, response size, pages and whole-task duration are bounded. Failure preserves previously published observations without treating them as new data.

PostgreSQL is the durable observation/member store; original files reside in the evidence volume. Existing backups cover `qd_ashare_*` tables and that volume. Monitoring validates recent board observations and reports corruption. One successful fetch does not complete historical theme coverage.

This module holds no model or brokerage keys, writes no paper orders, reviews no daily rules and approves no strategies. Live execution and automatic AI promotion remain prohibited.

## Operational entry points

After migration in the backend environment, ingest one exact provider name:

```bash
python -m app.ashare.themes ingest --name 人工智能 --evidence-dir /app/data/evidence/themes --timeout 60
```

Use the existing `ASHARE_DATABASE_URL` or `DATABASE_URL`; never print or paste connection credentials. In the deployed stack run inside the existing paper container, without exposing a host database port. This does not add an account watchlist item or submit an order.

To inspect a full directory separately, use `python -m app.ashare.themes catalog --evidence-dir /app/data/evidence/themes --timeout 30`. Catalog output is a raw inspection archive, not a durable membership record. Only successful `ingest` records establish stored themes; an extra catalog request is not needed before ingestion.

Use the actual board code from the receipt with `python -m app.ashare.themes snapshot --board-code BOARD_CODE_FROM_RECEIPT --limit 100 --offset 0`. Replace the placeholder with the returned code. An optional timezone-aware ISO `--known-at` queries versions already known at that time; it cannot write or backfill historical membership.
