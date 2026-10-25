"""Company-aware local candidate retrieval with source-backed activity profiles."""
import asyncio
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import hashlib
import re
import threading

from ingestion.database import connect
from orchestrator.domain.errors import DependencyUnavailable
from orchestrator.domain.models import NewsItem,RetrievalResult,SourceKind
from .needs import balanced_candidates,historical_retrospective,monitoring_queries,profile_context_queries
from .news import PostgresNewsRetriever


class ActivityProfiles:
    def __init__(self,profiles):self.profiles=profiles;self.activities=None

    async def warmup(self):
        await self.profiles.warmup()
        self.activities=await asyncio.to_thread(self._load)

    @staticmethod
    def _load():
        with connect() as c:
            c.execute('SET TRANSACTION READ ONLY')
            c.execute("SET LOCAL statement_timeout='5s'")
            return dict(c.execute('SELECT synthetic_u,mainokved_name FROM insights.companies'))

    async def get(self,company_id):
        if self.activities is None:raise RuntimeError('Profile warmup required')
        p=await self.profiles.get(company_id)
        return replace(p,activity=self.activities.get(company_id) or '') if p else None


class LocalFeedRetriever:
    def __init__(self,lexical=None,*,include_context=False):
        if type(include_context) is not bool:raise ValueError("include_context must be boolean")
        self.include_context=include_context
        self.lexical=lexical or PostgresNewsRetriever(bank_enabled=False)
        self.encoder=None;self.index=None;self._lock=threading.Lock()

    async def warmup(self):await asyncio.to_thread(self._load)

    def _load(self):
        from .semantic import LocalE5,SemanticNewsIndex
        self.encoder=LocalE5();self.index=SemanticNewsIndex()

    def _dense(self,profile,as_of,since):
        if self.index is None:raise RuntimeError('Index warmup required')
        if not self._lock.acquire(blocking=False):
            raise DependencyUnavailable('retriever','локальная модель занята')
        try:
            queries=monitoring_queries(profile)
            if self.include_context:
                queries+=tuple(q.text for q in profile_context_queries(profile))
            return [[n['news_id'] for n in self.index.search(self.encoder.query(q),
                as_of=as_of,published_after=since,limit=40)] for q in queries]
        finally:self._lock.release()

    async def retrieve(self,query):
        if query.question is not None or query.company is None or not query.company.activity:
            return await self.lexical.retrieve(query)
        if type(query.limit) is not int or not 1<=query.limit<=100:
            raise ValueError('limit must be 1..100')
        as_of=query.as_of or datetime.now(timezone.utc)
        since=query.published_after or as_of-timedelta(days=90)
        if as_of.utcoffset() is None or since.utcoffset() is None or since>as_of:
            raise ValueError('Invalid aware date bounds')
        # TaskGroup cancels siblings on failure; native inference may finish its
        # bounded batch after cancellation, guarded against concurrent re-entry.
        async with asyncio.TaskGroup() as group:
            lexical=group.create_task(self.lexical.retrieve(replace(query,limit=min(query.limit,40))))
            dense=group.create_task(asyncio.to_thread(self._dense,query.company,as_of,since))
        sparse={n.news_id:n for n in lexical.result().news}
        rankings=dense.result()+[list(sparse)]
        # Filter before quota allocation so historical pieces don't consume slots.
        def historical(doc_id):
            source=sparse.get(doc_id)
            if source is not None:
                return historical_retrospective(source.title,source.text,as_of)
            source=self.index.documents[doc_id]
            return historical_retrospective(source['title'],source['text'],as_of)
        rankings=[[d for d in ids if not historical(d)] for ids in rankings]
        ids=balanced_candidates(rankings,query.limit)
        result=[]
        for rank,d in enumerate(ids,1):
            if d in sparse:
                result.append(replace(sparse[d],retrieval_score=1/rank));continue
            n=self.index.documents[d]
            normalized=' '.join(re.findall(r'\w+',n['title'].casefold().replace('ё','е')))
            key=normalized+'|'+n['published_at'].astimezone(timezone.utc).date().isoformat()
            result.append(NewsItem(news_id=d,cluster_id='headline:'+hashlib.sha256(key.encode()).hexdigest()[:24],
                title=n['title'],text=n['text'],url=n['url'],published_at=n['published_at'],
                source_kind=SourceKind(n['source_kind']),retrieval_score=1/rank))
        return RetrievalResult(tuple(result))
