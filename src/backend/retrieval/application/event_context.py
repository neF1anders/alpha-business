"""Event + company -> traceable bank context. Explicit lexical baseline, no LLM.

Accepts an unseen NewsItem directly; it need not exist in the news table.
Retrieved background is not evidence that the bank commented on the new event.
"""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator.domain.models import BankMaterial, EventContext
from orchestrator.domain.ranking import TagReranker

from ..domain.bank_materials import bank_materials
from ..domain.event_rules import PROFILE_HINTS, TOPICS, validate
from ..domain.query import natural_query
from ..domain.search_rules import phrase_query, rules_for
from .ports import NewsSearchStore, ReportFileCatalog


async def build_event_context(session, report_files: ReportFileCatalog, event, profile, *,
                              as_of, limit=5, reports_root=None):
    validate(event, profile, as_of, limit)
    lead = event.text[:800]
    detected = await session.detect_topics(title=event.title, lead=lead,
        rules=[dict(tag=k,query=phrase_query(v)) for k,v in TOPICS.items()])
    # A focused headline beats incidental factors mentioned later in an article.
    focused = [r['tag'] for r in detected if r['in_title']]
    topics = tuple(focused or [r['tag'] for r in detected])
    tag_rules = rules_for(profile.tags)
    # Explicit activity-code bridge; do not mistake a fishing company for a farm.
    tag_rules += [dict(tag='okved:03', query=phrase_query(TOPICS['fish']))]
    tags = await session.detect_topics(title=event.title, lead=lead, rules=tag_rules)
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
        rows = await session.fetch_bank_chunks(query=query,anchor=anchor,as_of=as_of.date(),
                                               since=as_of.date()-timedelta(days=365),limit=limit)
        if reports_root is not None:
            root = Path(reports_root).resolve()
            rows = [r for r in rows if report_files.has_pdf(root, r['source_file'])]
        converted = bank_materials(rows,{r['document_id']:'/v1/materials/'+r['document_id'] for r in rows})
        materials = tuple(BankMaterial(**m, news_ids=(event.news_id,),
                                      published_at=r['published_at']) for m,r in zip(converted,rows))
        status = 'background_found' if materials else 'no_eligible_bank_context'
    return EventContext(event.news_id,profile.company_id,topics,tuple(sorted(event_tags)),
                        matched,scored.score,materials,status,trace)


class EventRetrieval:
    def __init__(self, store: NewsSearchStore, report_files: ReportFileCatalog, *, timeout_s=0.65,
                 reports_root=None, max_concurrency=4):
        if not 0 < timeout_s <= 5 or not 1 <= max_concurrency <= 16:
            raise ValueError('Invalid retrieval bounds')
        self.store, self.report_files = store, report_files
        self.timeout_s, self.reports_root = timeout_s,reports_root
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def retrieve(self, event, profile, *, as_of=None, limit=5):
        as_of = as_of or datetime.now(timezone.utc)
        validate(event,profile,as_of,limit)
        async with asyncio.timeout(self.timeout_s):
            async with self._semaphore:
                async with self.store.session(max(1,int(self.timeout_s*1000))) as session:
                    return await build_event_context(session,self.report_files,event,profile,
                                                     as_of=as_of,limit=limit,
                                                     reports_root=self.reports_root)
