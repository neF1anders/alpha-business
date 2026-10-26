"""PostgreSQL storage for news embeddings and the read-only index snapshot."""
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ingestion.infrastructure import postgres

from ..domain.embedding import source_hash

SCHEMA=Path(__file__).resolve().parents[4]/'sql/embeddings.sql'


class PostgresEmbeddingStore:
    def ensure_space(self,space_id,recipe):
        with postgres.connect() as c:
            c.execute(SCHEMA.read_text())
            c.execute('INSERT INTO insights.embedding_spaces VALUES (%s,%s,now()) ON CONFLICT DO NOTHING',
                      (space_id,Jsonb(recipe)))
            saved=c.execute('SELECT recipe FROM insights.embedding_spaces WHERE space_id=%s',(space_id,)).fetchone()[0]
            if saved!=recipe:raise ValueError('Conflicting embedding recipe')

    def read_news(self):
        with postgres.connect() as c:
            c.execute('SET TRANSACTION READ ONLY')
            return c.execute('SELECT news_id,title,text FROM insights.news_articles ORDER BY news_id').fetchall()

    def read_indexed_summary(self,space_id):
        with postgres.connect() as c:
            c.execute('SET TRANSACTION READ ONLY')
            return c.execute('''SELECT news_id,min(source_hash),count(*),min(chunk_count),max(chunk_count),
                            min(chunk_index),max(chunk_index)
                            FROM insights.news_embeddings WHERE space_id=%s GROUP BY news_id''',(space_id,)).fetchall()

    def save_group(self,space_id,group,metadata,vectors):
        with postgres.connect() as c:
            # Serialize only writes, not expensive inference. Each document is atomic.
            c.execute('SELECT pg_advisory_xact_lock(521104)')
            for doc,title,text,h in group:
                current=c.execute('SELECT title,text FROM insights.news_articles WHERE news_id=%s FOR SHARE',(doc,)).fetchone()
                if current is None or source_hash(*current)!=h:raise ValueError('Source changed during encoding')
            for meta,vector in zip(metadata,vectors):
                c.execute('''INSERT INTO insights.news_embeddings
                    (space_id,news_id,source_hash,chunk_index,chunk_count,token_start,token_end,embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',(space_id,*meta,vector.tobytes()))

    def load_snapshot(self,space_id):
        with postgres.connect() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            with c.cursor(row_factory=dict_row) as cur:
                cur.execute('SELECT news_id,title,text,url,source_kind,published_at FROM insights.news_articles ORDER BY news_id')
                documents={r['news_id']:r for r in cur.fetchall()}
            rows=c.execute('''SELECT news_id,source_hash,chunk_index,chunk_count,token_start,token_end,embedding
                FROM insights.news_embeddings WHERE space_id=%s ORDER BY news_id,chunk_index''',(space_id,)).fetchall()
        return documents,rows
