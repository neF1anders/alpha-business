CREATE SCHEMA IF NOT EXISTS insights;
CREATE TABLE IF NOT EXISTS insights.csv_snapshot (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    manifest jsonb NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);
-- Entities without a company profile are retained as graph endpoints.
CREATE TABLE IF NOT EXISTS insights.company_entities (company_id text PRIMARY KEY);
CREATE TABLE IF NOT EXISTS insights.companies (
    synthetic_u text PRIMARY KEY REFERENCES insights.company_entities,
    org_type text, okfs_name text, company_age_years_gradation text,
    eco_mark_label text, mainokved_code text, mainokved_name text,
    negativemark boolean,
    amount_workers_fns_gradation text, amount_workers_spark_gradation text,
    cl_type text, cl_product_rko_mark boolean, cl_product_gar_mark boolean,
    cl_product_loa_mark boolean, cl_product_lea_mark boolean,
    cl_product_dep_mark boolean, cl_product_zpp_mark boolean,
    fts_flag boolean, fts_country_name text, factoring_mark boolean,
    revenue_company numeric CHECK (revenue_company >= 0), group_id text,
    city text, region text, source_row bigint NOT NULL
);
COMMENT ON COLUMN insights.companies.negativemark IS 'Наличие признака ключевого собственника (словарь предоставлен пользователем)';
COMMENT ON COLUMN insights.companies.revenue_company IS 'Годовая выручка; единицы не заданы. Значение 1000 сохранено.';
CREATE INDEX IF NOT EXISTS companies_group ON insights.companies(group_id);
CREATE INDEX IF NOT EXISTS companies_okved ON insights.companies(mainokved_code);
CREATE TABLE IF NOT EXISTS insights.person_links (
    synthetic_f text NOT NULL,
    city_name text,
    rc_name text,
    synthetic_u text NOT NULL REFERENCES insights.company_entities,
    source_row bigint NOT NULL,
    PRIMARY KEY (source_row)
);
CREATE INDEX IF NOT EXISTS person_links_person ON insights.person_links(synthetic_f);
CREATE INDEX IF NOT EXISTS person_links_company ON insights.person_links(synthetic_u);
CREATE TABLE IF NOT EXISTS insights.payments (
    beneficiary text NOT NULL REFERENCES insights.company_entities,
    payer text NOT NULL REFERENCES insights.company_entities,
    pay_amt numeric NOT NULL CHECK (pay_amt > 0),
    pay_cnt bigint NOT NULL CHECK (pay_cnt > 0),
    source_row bigint NOT NULL,
    PRIMARY KEY (payer, beneficiary),
    CHECK (payer <> beneficiary)
);
CREATE INDEX IF NOT EXISTS payments_beneficiary ON insights.payments(beneficiary);
CREATE TABLE IF NOT EXISTS insights.documents (
    document_id text PRIMARY KEY,
    source_file text NOT NULL UNIQUE,
    title text NOT NULL,
    sha256 text NOT NULL,
    published_at date,
    date_source text,
    page_count integer NOT NULL,
    extraction_settings jsonb NOT NULL,
    artifact_sha256 text NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS insights.document_pages (
    document_id text NOT NULL REFERENCES insights.documents ON DELETE CASCADE,
    page_number integer NOT NULL,
    text text NOT NULL,
    method text NOT NULL,
    confidence real,
    needs_review boolean NOT NULL,
    PRIMARY KEY (document_id, page_number)
);
CREATE TABLE IF NOT EXISTS insights.document_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL,
    page_number integer NOT NULL,
    chunk_index integer NOT NULL,
    start_char integer NOT NULL,
    end_char integer NOT NULL,
    text text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('russian', text)) STORED,
    FOREIGN KEY (document_id,page_number) REFERENCES insights.document_pages ON DELETE CASCADE,
    UNIQUE (document_id,page_number,chunk_index)
);
CREATE INDEX IF NOT EXISTS chunks_search ON insights.document_chunks USING gin(search_vector);
-- Additive migration: safe for databases populated by the initial PDF loader.
ALTER TABLE insights.documents ADD COLUMN IF NOT EXISTS source_type text NOT NULL DEFAULT 'pdf';
ALTER TABLE insights.documents ADD COLUMN IF NOT EXISTS source_url text;
ALTER TABLE insights.documents ADD COLUMN IF NOT EXISTS publisher text;
ALTER TABLE insights.documents ADD COLUMN IF NOT EXISTS external_id text;
ALTER TABLE insights.documents ADD COLUMN IF NOT EXISTS published_at_timestamp timestamptz;
ALTER TABLE insights.documents ADD COLUMN IF NOT EXISTS source_metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE insights.document_chunks ADD COLUMN IF NOT EXISTS tags text[] NOT NULL DEFAULT '{}';
ALTER TABLE insights.document_chunks ADD COLUMN IF NOT EXISTS key_rate boolean NOT NULL DEFAULT false;
ALTER TABLE insights.document_chunks ADD COLUMN IF NOT EXISTS fx boolean NOT NULL DEFAULT false;
ALTER TABLE insights.document_chunks ADD COLUMN IF NOT EXISTS commodities boolean NOT NULL DEFAULT false;
ALTER TABLE insights.document_chunks ADD COLUMN IF NOT EXISTS consumer_demand boolean NOT NULL DEFAULT false;
ALTER TABLE insights.document_chunks ADD COLUMN IF NOT EXISTS construction_market boolean NOT NULL DEFAULT false;
ALTER TABLE insights.document_chunks ADD COLUMN IF NOT EXISTS tagging_version text;
ALTER TABLE insights.document_chunks ADD COLUMN IF NOT EXISTS tag_metadata jsonb NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS chunks_tags ON insights.document_chunks USING gin(tags);
CREATE INDEX IF NOT EXISTS documents_external_id ON insights.documents(external_id);
CREATE OR REPLACE VIEW insights.rag_chunks AS
SELECT c.chunk_id,c.document_id,d.title,d.source_file,d.published_at,d.date_source,
       c.page_number,c.text,c.start_char,c.end_char,p.method,p.confidence,c.search_vector,
       d.source_type,d.source_url,d.publisher,d.published_at_timestamp,
       c.tags,c.key_rate,c.fx,c.commodities,c.consumer_demand,c.construction_market,
       c.tagging_version,c.tag_metadata
FROM insights.document_chunks c
JOIN insights.documents d USING(document_id)
JOIN insights.document_pages p USING(document_id,page_number)
WHERE NOT p.needs_review;

-- Audited news snapshots and immutable source articles. Independent of bank PDFs.
CREATE TABLE IF NOT EXISTS insights.news_imports (
    import_sha256 text PRIMARY KEY CHECK (import_sha256 ~ '^[0-9a-f]{64}$'),
    source_path text NOT NULL,
    row_count integer NOT NULL CHECK (row_count > 0),
    report jsonb NOT NULL,
    audit jsonb NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS insights.news_articles (
    news_id text PRIMARY KEY,
    url text NOT NULL UNIQUE,
    title text NOT NULL,
    text text NOT NULL CHECK (length(text) >= 200),
    source text NOT NULL,
    source_kind text NOT NULL CHECK (source_kind IN ('primary', 'media', 'bank')),
    published_at timestamptz NOT NULL,
    event_at timestamptz,
    fetched_at timestamptz NOT NULL,
    text_sha256 text NOT NULL UNIQUE CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    categories text[] NOT NULL DEFAULT '{}',
    source_record jsonb NOT NULL,
    raw_receipt jsonb NOT NULL,
    first_import_sha256 text NOT NULL REFERENCES insights.news_imports(import_sha256),
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('russian', title), 'A') ||
        setweight(to_tsvector('russian', text), 'B')
    ) STORED
);
CREATE INDEX IF NOT EXISTS news_search ON insights.news_articles USING gin(search_vector);
CREATE INDEX IF NOT EXISTS news_published ON insights.news_articles(published_at DESC);
CREATE TABLE IF NOT EXISTS insights.news_import_items (
    import_sha256 text NOT NULL REFERENCES insights.news_imports(import_sha256),
    news_id text NOT NULL REFERENCES insights.news_articles(news_id),
    PRIMARY KEY (import_sha256, news_id)
);
COMMENT ON TABLE insights.news_articles IS
    'Extracted publications, not event clusters or company relevance labels. Original source_record retained.';
