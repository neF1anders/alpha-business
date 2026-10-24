-- Lexical retrieval with Russian morphology. This is PostgreSQL FTS, not BM25.
SELECT chunk_id, title, source_file, page_number, published_at, text,
       ts_rank_cd(search_vector, websearch_to_tsquery('russian', 'ключевая ставка')) AS score
FROM insights.rag_chunks
WHERE search_vector @@ websearch_to_tsquery('russian', 'ключевая ставка')
ORDER BY score DESC LIMIT 10;

-- Require ALL tags (case and canonical spelling matter). Use && for ANY overlap.
SELECT chunk_id, source_file, source_url, page_number, tags, text
FROM insights.rag_chunks
WHERE tags @> ARRAY['factor:fx', 'country:КИТАЙ']::text[]
LIMIT 10;

-- Boolean factors, semantic sector and countries recognized outside the company vocabulary.
SELECT chunk_id, key_rate::int, fx::int, commodities::int, consumer_demand::int,
       construction_market::int, tag_metadata->'sector' AS sector,
       tag_metadata->'unmapped_countries' AS unmapped_countries
FROM insights.rag_chunks WHERE tagging_version IS NOT NULL LIMIT 10;

-- Keep counterparties even when their profile is unknown.
SELECT p.beneficiary, p.pay_amt, p.pay_cnt, c.mainokved_name, c.region
FROM insights.payments p
LEFT JOIN insights.companies c ON c.synthetic_u = p.beneficiary
WHERE p.payer = 'U_144100'
ORDER BY p.pay_amt DESC LIMIT 10;

-- Companies connected through the same anonymized person.
SELECT DISTINCT b.synthetic_u
FROM insights.person_links a
JOIN insights.person_links b USING (synthetic_f)
WHERE a.synthetic_u = 'U_144100' AND b.synthetic_u <> a.synthetic_u;
