"""Event + company -> traceable bank context. Explicit lexical baseline, no LLM.

Accepts an unseen NewsItem directly; it need not exist in the news table.
Retrieved background is not evidence that the bank commented on the new event.
"""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from orchestrator.domain.models import BankMaterial, CompanyProfile, EventContext, NewsItem
from orchestrator.domain.ranking import TagReranker
from retrieval.adapter import bank_materials
from retrieval.news import open_connection, phrase_query, rules_for
from retrieval.postgres import NATURAL_SQL
from retrieval.query import natural_query

# Narrow subjects anchor bank retrieval. Broad words such as 'рынок', 'банк',
# 'производство' cannot turn an unrelated bank report into event context.
TOPICS = {
    'policy_rate': ('ключевая ставка', 'денежно-кредитная политика', 'ставка ЦБ'),
    'inflation': ('инфляция', 'потребительские цены'),
    'labor': ('рынок труда', 'безработица', 'дефицит кадров', 'заработная плата'),
    'consumption': ('потребительский спрос', 'потребительские расходы', 'розничные продажи'),
    'fx': ('курс рубля', 'курс доллара', 'курс юаня', 'валютный рынок'),
    'gold': ('золото', 'золотодобыча'),
    'steel': ('сталь', 'металлургия', 'сталелитейный'),
    'housing': ('ипотека', 'новостройки', 'жилищное строительство'),
    'fish': ('рыболовство', 'вылов рыбы', 'вылов лосося', 'промысловая разведка'),
    'transport': ('грузоперевозки', 'грузовые перевозки', 'фрахт'),
    'datacenters': ('дата-центр', 'центр обработки данных', 'ЦОД'),
}
PROFILE_HINTS = {'product:credit': 'кредит', 'product:leasing': 'лизинг',
                 'product:factoring': 'факторинг', 'product:deposit': 'депозит'}
# Do not infer borrowing from sector/size or foreign-currency exposure from FTS.

DETECT_SQL = """
WITH vectors AS (
 SELECT to_tsvector('russian',%(title)s) AS headline,
        to_tsvector('russian',%(lead)s) AS lead,
        replace(lower(%(title)s),'ё','е') AS raw_title,
        replace(lower(%(lead)s),'ё','е') AS raw_lead
), rules AS (
 SELECT tag,websearch_to_tsquery('russian',query) AS q,geo_pattern
 FROM jsonb_to_recordset(%(rules)s::jsonb) AS r(tag text,query text,geo_pattern text)
)
SELECT tag, CASE WHEN geo_pattern IS NULL THEN headline @@ q
            ELSE raw_title ~ geo_pattern END AS in_title FROM rules CROSS JOIN vectors
WHERE CASE WHEN geo_pattern IS NULL THEN headline @@ q OR lead @@ q
           ELSE raw_title ~ geo_pattern OR raw_lead ~ geo_pattern END ORDER BY tag
"""

# Anchor must occur in the actual chunk, not only in the document title or page
# heading. NATURAL_SQL still adds those fields for ordering and diversification.
EVENT_BANK_SQL = NATURAL_SQL.replace(
    'AND NOT (p.text',
    "AND c.search_vector @@ websearch_to_tsquery('russian', %(anchor)s)\n"
    "      AND length(regexp_replace(c.text, '[^[:alpha:]]', '', 'g'))::float / GREATEST(length(c.text),1) >= 0.65\n"
    "      AND NOT (p.text",
).replace(
    'ORDER BY matched_terms DESC, score DESC',
    "ORDER BY ts_rank_cd(to_tsvector('russian', title), websearch_to_tsquery('russian', %(anchor)s),32) DESC, matched_terms DESC, score DESC",
)


def validate(event, profile, as_of, limit):
    if not isinstance(event, NewsItem) or not isinstance(profile, CompanyProfile):
        raise ValueError('NewsItem and CompanyProfile required')
    if not event.news_id or not event.title.strip() or len(event.title) > 1000 or not event.text.strip() or len(event.text) > 100000:
        raise ValueError('Nonempty event ID/title/text required; title <=1000, text <=100000')
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or event.published_at.tzinfo is None:
        raise ValueError('Timezone-aware timestamps required')
    if event.published_at > as_of:
        raise ValueError('Event is later than as_of')
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError('limit must be 1..5')
    if len(profile.tags) > 100 or any(not isinstance(t,str) or len(t)>128 for t in profile.tags):
        raise ValueError('At most 100 bounded company tags required')


async def event_context(conn, event, profile, *, as_of, limit=5, reports_root=None):
    validate(event, profile, as_of, limit)
    lead = event.text[:800]
    params = dict(title=event.title, lead=lead,
                  rules=Jsonb([dict(tag=k,query=phrase_query(v)) for k,v in TOPICS.items()]))
    detected = await (await conn.execute(DETECT_SQL, params)).fetchall()
    # A focused headline beats incidental factors mentioned later in an article.
    focused = [r['tag'] for r in detected if r['in_title']]
    topics = tuple(focused or [r['tag'] for r in detected])
    tag_rules = rules_for(profile.tags)
    # Explicit activity-code bridge; do not mistake a fishing company for a farm.
    tag_rules += [dict(tag='okved:03', query=phrase_query(TOPICS['fish']))]
    tags = await (await conn.execute(DETECT_SQL, dict(title=event.title, lead=lead,
                                                       rules=Jsonb(tag_rules)))).fetchall()
    event_tags = frozenset(r['tag'] for r in tags)
    annotated = replace(event, tags=event_tags, retrieval_score=0.0)
    scored = TagReranker(min_score=0, min_relevance=0).rerank(profile,[annotated],1,
                                require_tag_match=False,now=as_of)[0]
    matched = tuple(sorted(event_tags & profile.tags))
    hints = [value for tag,value in PROFILE_HINTS.items() if tag in profile.tags]
    trace = dict(as_of=as_of.isoformat(), published_at=event.published_at.isoformat(),
                 classification_input='headline_and_first_800_characters',
                 bank_text_alpha_ratio_min=0.65, ranking='topic_document_then_term_coverage',
                 profile_hints=hints, company_signal_type='heuristic_not_impact_probability',
                 evidence_role='dated_background_not_current_bank_verdict',
                 rules_version='event_lexical_v2', query=None, anchor=None)
    materials = ()
    status = 'no_supported_topic'
    if topics:
        anchor = phrase_query([phrase for t in topics for phrase in TOPICS[t]])
        query = natural_query(' '.join(phrase for t in topics for phrase in TOPICS[t])
                              + ' ' + ' '.join(hints))[:2000]
        trace.update(query=query,anchor=anchor)
        params = dict(query=query,anchor=anchor,as_of=as_of.date(),
                      since=as_of.date()-timedelta(days=365),sources=None,
                      undated=False,per_document=2,limit=limit)
        rows = await (await conn.execute(EVENT_BANK_SQL,params)).fetchall()
        if reports_root is not None:
            root = Path(reports_root).resolve()
            rows = [r for r in rows if (root/r['source_file']).resolve().parent==root
                    and (root/r['source_file']).suffix.lower()=='.pdf' and (root/r['source_file']).is_file()]
        converted = bank_materials(rows,{r['document_id']:'/v1/materials/'+r['document_id'] for r in rows})
        materials = tuple(BankMaterial(**m, news_ids=(event.news_id,),
                                      published_at=r['published_at']) for m,r in zip(converted,rows))
        status = 'background_found' if materials else 'no_eligible_bank_context'
    return EventContext(event.news_id,profile.company_id,topics,tuple(sorted(event_tags)),
                        matched,scored.score,materials,status,trace)


class PostgresEventRetriever:
    def __init__(self, *, timeout_s=0.65, reports_root=None, max_concurrency=4):
        if not 0 < timeout_s <= 5 or not 1 <= max_concurrency <= 16:
            raise ValueError('Invalid retrieval bounds')
        self.timeout_s, self.reports_root = timeout_s,reports_root
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def retrieve(self, event, profile, *, as_of=None, limit=5):
        as_of = as_of or datetime.now(timezone.utc)
        validate(event,profile,as_of,limit)
        async with asyncio.timeout(self.timeout_s):
            async with self._semaphore:
                async with await open_connection() as conn:
                    await conn.execute('SET TRANSACTION READ ONLY')
                    await conn.execute("SELECT set_config('statement_timeout',%s,true)",
                                       (str(max(1,int(self.timeout_s*1000))),))
                    return await event_context(conn,event,profile,as_of=as_of,limit=limit,
                                               reports_root=self.reports_root)
