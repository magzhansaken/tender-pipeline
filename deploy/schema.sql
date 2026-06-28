-- ════════════════════════════════════════════════════════════
--  TenderView — схема БД
--  Один справочник обработанных лотов. Веб-слой только читает.
--  Запускается автоматически при первом старте контейнера postgres
--  (файл смонтирован в /docker-entrypoint-initdb.d/).
-- ════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- быстрый поиск по подстроке (ILIKE)

CREATE TABLE IF NOT EXISTS lots (
    id               bigserial PRIMARY KEY,
    row_id           bigint UNIQUE,
    name             text,
    status           text,
    category         text,
    category_type    text,
    brand_in_spec    text,
    model_in_spec    text,
    found_brand      text,
    found_model      text,
    found_product    text,
    source_url       text,
    source_site      text,
    matched_specs    jsonb DEFAULT '[]'::jsonb,
    missing_specs    jsonb DEFAULT '[]'::jsonb,
    conflicts        jsonb DEFAULT '[]'::jsonb,
    confidence       int,
    reason           text,
    candidates_found int,
    time_sec         real,
    lot_price        numeric,
    purchase_price   numeric,
    margin           numeric,
    margin_pct       numeric,
    customer         text,
    quantity         int,
    unit             text,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lots_status       ON lots (status);
CREATE INDEX IF NOT EXISTS idx_lots_category     ON lots (category);
CREATE INDEX IF NOT EXISTS idx_lots_confidence   ON lots (confidence DESC);
CREATE INDEX IF NOT EXISTS idx_lots_updated      ON lots (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_lots_name_trgm    ON lots USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_lots_product_trgm ON lots USING gin (found_product gin_trgm_ops);
