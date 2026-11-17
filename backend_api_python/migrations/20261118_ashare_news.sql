-- Global public company-announcement evidence; no account or trading state.
CREATE TABLE IF NOT EXISTS qd_ashare_news_receipts (
    id text PRIMARY KEY,
    received_at timestamptz NOT NULL,
    source text NOT NULL CHECK (source = 'cninfo'),
    source_url text NOT NULL CHECK (source_url = 'https://www.cninfo.com.cn/new/hisAnnouncement/query'),
    source_hash text NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    evidence_path text NOT NULL,
    topics jsonb NOT NULL CHECK (jsonb_typeof(topics) = 'array'),
    item_count integer NOT NULL CHECK (item_count BETWEEN 0 AND 15),
    metadata jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS qd_ashare_news_receipts_known ON qd_ashare_news_receipts(received_at DESC,id DESC);
CREATE TABLE IF NOT EXISTS qd_ashare_news_items (
    id text PRIMARY KEY,
    receipt_id text NOT NULL REFERENCES qd_ashare_news_receipts(id),
    article_id text NOT NULL,
    published_at timestamptz,
    source_url text NOT NULL,
    source_hash text NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    details jsonb NOT NULL,
    UNIQUE(receipt_id,article_id)
);
CREATE OR REPLACE FUNCTION qd_ashare_news_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'news_evidence_is_append_only';
END;
$$;
DROP TRIGGER IF EXISTS qd_ashare_news_receipts_append_only ON qd_ashare_news_receipts;
CREATE TRIGGER qd_ashare_news_receipts_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON qd_ashare_news_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION qd_ashare_news_append_only();
DROP TRIGGER IF EXISTS qd_ashare_news_items_append_only ON qd_ashare_news_items;
CREATE TRIGGER qd_ashare_news_items_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON qd_ashare_news_items
    FOR EACH STATEMENT EXECUTE FUNCTION qd_ashare_news_append_only();
