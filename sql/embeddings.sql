-- Derived local embeddings only. Source news are never replaced by this index.
CREATE TABLE IF NOT EXISTS insights.embedding_spaces (
    space_id text PRIMARY KEY,
    recipe jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS insights.news_embeddings (
    space_id text NOT NULL REFERENCES insights.embedding_spaces,
    news_id text NOT NULL REFERENCES insights.news_articles(news_id),
    source_hash text NOT NULL CHECK (length(source_hash)=64),
    chunk_index integer NOT NULL CHECK (chunk_index>=0),
    chunk_count integer NOT NULL CHECK (chunk_count>0 AND chunk_index<chunk_count),
    token_start integer NOT NULL CHECK (token_start>=0),
    token_end integer NOT NULL CHECK (token_end>=token_start),
    embedding bytea NOT NULL CHECK (octet_length(embedding)=1536),
    PRIMARY KEY (space_id,news_id,chunk_index)
);
