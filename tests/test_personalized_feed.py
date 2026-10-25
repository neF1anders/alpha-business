import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import unittest
import threading
from unittest.mock import Mock

from orchestrator.domain.errors import DependencyUnavailable
from orchestrator.domain.models import NewsQuery
from orchestrator.domain.news_quality import historical_retrospective
from orchestrator.domain.ranking import TagReranker
from orchestrator.application.use_cases import BannersCommand,GetBanners,Limits
from retrieval.feed import LocalFeedRetriever
from retrieval.needs import balanced_candidates,monitoring_queries,profile_context_queries
from retrieval.reranker import LocalPairReranker
from tests.orchestrator_support import NOW,PROFILE,FakeBranch,FakeProfiles,FakeRetriever,news

P=replace(PROFILE,activity='Торговля оптовая рыбой')


class TopicQualityTests(unittest.TestCase):
    def test_queries_depend_on_facts_not_identity_or_unconfirmed_contracts(self):
        queries=monitoring_queries(P)
        self.assertEqual(queries,monitoring_queries(replace(P,company_id='unseen')))
        self.assertTrue(all(P.activity in q for q in queries))
        self.assertFalse(any('поставщик компании' in q for q in queries))
        self.assertEqual(P.with_role('ceo').activity,P.activity)
        with self.assertRaises(ValueError):monitoring_queries(replace(P,activity=''))

    def test_context_queries_use_source_products_and_country_links(self):
        plain=replace(P,tags=frozenset({'sector:trade','factor:key_rate','factor:fx','product:rko'}))
        self.assertEqual(profile_context_queries(plain),())
        enriched=replace(plain,tags=plain.tags|{'product:credit','product:leasing','country:КИТАЙ','country:ПЕРУ'})
        queries=profile_context_queries(enriched)
        self.assertEqual([q.kind for q in queries],['interest_rates','international_settlements'])
        self.assertEqual(len(queries),2)  # Products/countries do not multiply channels.
        self.assertTrue(all(set(q.source_tags)<=enriched.tags for q in queries))
        self.assertEqual(queries,profile_context_queries(replace(enriched,company_id='unseen',activity='Другая деятельность')))
        self.assertFalse(any('поставщик компании' in q.text or 'плавающ' in q.text for q in queries))
        for product in ('credit','leasing','factoring','deposit'):
            with self.subTest(product=product):
                self.assertEqual(len(profile_context_queries(replace(plain,tags=frozenset({'product:'+product})))),1)

    def test_retrospective_and_present_event_with_historical_background(self):
        self.assertTrue(historical_retrospective('История зарплат','1926 год — важный год в истории СССР.',NOW))
        self.assertFalse(historical_retrospective('Сегодня банк обновил тарифы','1926 год — год основания банка.',NOW))
        self.assertFalse(historical_retrospective('Кредитование в 2026 году','1926 год — год основания банка.',NOW))
        self.assertFalse(historical_retrospective('Новый завод', '1926 год — дата основания компании. Начато строительство.',NOW))
        self.assertFalse(historical_retrospective('Цена выросла','Цены достигли максимума с 1990 года.',NOW))
        history=replace(news(1,{'factor:key_rate'}),text='1926 год — важный год в истории СССР.')
        future=replace(news(2,{'factor:key_rate'}),published_at=NOW+timedelta(days=1))
        self.assertEqual(TagReranker().rerank(PROFILE,[history,future],5,now=NOW),[])
        self.assertEqual(len(TagReranker().rerank(PROFILE,[history],5,now=NOW,require_tag_match=False)),1)

    def test_balanced_union_preserves_channels_without_duplicate_votes(self):
        self.assertEqual(balanced_candidates([['a','b','c'],['a','d','e'],['f']],5),['a','f','b','d','c'])


class PairRankingTests(unittest.TestCase):
    def make_ranker(self,scores):
        r=LocalPairReranker();r.tokenizer=SimpleNamespace(encode=lambda *a,**k:[1])
        r.score_pairs=Mock(return_value=scores);return r

    def test_missing_rule_tags_and_negative_logits_do_not_drop_semantic_candidate(self):
        r=self.make_ranker([-2.,-1.])
        a=news(1,set());b=news(2,set())
        ranked=r.rerank(P,[a,b],5,now=NOW)
        self.assertEqual([x.item.news_id for x in ranked],['n2','n1'])
        self.assertTrue(all(0<x.score<1 for x in ranked))

    def test_history_future_duplicates_and_source_text_are_preserved(self):
        r=self.make_ranker([1.,2.])
        long=replace(news(1,set(),cluster='same'),text='a'*10000)
        duplicate=news(2,set(),cluster='same')
        history=replace(news(3,set()),text='1926 год — история экономики СССР.')
        future=replace(news(4,set()),published_at=NOW+timedelta(days=1))
        ranked=r.rerank(P,[long,duplicate,history,future],5,now=NOW)
        self.assertEqual([x.item.news_id for x in ranked],['n2'])
        self.assertEqual(len(long.text),10000)
        self.assertLess(len(r.score_pairs.call_args.args[0][0][1]),2500)

    def test_busy_model_and_error_release(self):
        r=self.make_ranker([]);r._lock.acquire()
        with self.assertRaises(DependencyUnavailable):r.rerank(P,[news(1,set())],5,now=NOW)
        r._lock.release();r.score_pairs.side_effect=RuntimeError('model failed')
        with self.assertRaises(RuntimeError):r.rerank(P,[news(1,set())],5,now=NOW)
        self.assertTrue(r._lock.acquire(blocking=False));r._lock.release()

    def test_chat_and_missing_activity_use_existing_ranking(self):
        r=self.make_ranker([]);item=news(1,{'factor:key_rate'})
        self.assertTrue(r.rerank(PROFILE,[item],5,now=NOW))
        self.assertTrue(r.rerank(P,[item],5,now=NOW,require_tag_match=False))
        r.score_pairs.assert_not_called()


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_channel_is_opt_in_and_keeps_same_candidate_budget(self):
        profile=replace(P,tags=P.tags|{'product:credit','country:КИТАЙ'})
        seen=[]
        f=LocalFeedRetriever(include_context=True)
        f.encoder=SimpleNamespace(query=lambda q: seen.append(q) or q)
        f.index=SimpleNamespace(search=lambda *a,**kw:[{'news_id':'n1'}])
        channels=f._dense(profile,NOW,NOW-timedelta(days=90))
        self.assertEqual(len(channels),5)
        self.assertEqual(len(seen),5)
        f.include_context=False
        self.assertEqual(len(f._dense(profile,NOW,NOW-timedelta(days=90))),3)
        self.assertFalse(LocalFeedRetriever().include_context)

    async def test_80_budget_extends_40_without_dropping_its_candidates(self):
        items=[news(i,set()) for i in range(120)]
        f=LocalFeedRetriever(FakeRetriever(items[80:120]))
        f._dense=lambda *a:[[n.news_id for n in items[i:i+40]] for i in (0,30,60)]
        f.index=SimpleNamespace(documents={n.news_id:dict(news_id=n.news_id,title=n.title,
            text=n.text,url=n.url,published_at=n.published_at,source_kind='media') for n in items})
        a=await f.retrieve(NewsQuery(P.tags,company=P,as_of=NOW,limit=40))
        b=await f.retrieve(NewsQuery(P.tags,company=P,as_of=NOW,limit=80))
        self.assertEqual(len(b.news),80)
        self.assertEqual(len({n.news_id for n in b.news}),80)
        self.assertEqual(b.news[:40],a.news)

    async def test_new_lexical_record_outside_startup_vector_snapshot_is_kept(self):
        fresh=news(2,{'factor:key_rate'});dense=news(1,set())
        legacy=FakeRetriever([fresh]);f=LocalFeedRetriever(legacy)
        f._dense=lambda *a:[[dense.news_id]]
        f.index=SimpleNamespace(documents={dense.news_id:dict(news_id=dense.news_id,title=dense.title,
            text=dense.text,url=dense.url,published_at=dense.published_at,source_kind='media')})
        result=await f.retrieve(NewsQuery(P.tags,company=P,as_of=NOW,limit=5))
        self.assertEqual({n.news_id for n in result.news},{'n1','n2'})
        await f.retrieve(NewsQuery(P.tags,company=P,question='ставка',as_of=NOW))
        self.assertEqual(legacy.queries[-1].question,'ставка')

    async def test_reranker_failure_is_degraded_and_receives_company_context(self):
        retriever=FakeRetriever([news(1,{'factor:key_rate'})])
        ranker=SimpleNamespace(rerank=Mock(side_effect=RuntimeError('private error')))
        case=GetBanners(FakeProfiles(P),retriever,ranker,FakeBranch(),Limits())
        result=await case.execute(BannersCommand(P.company_id))
        self.assertFalse(result.banners)
        self.assertTrue(result.degraded)
        self.assertNotIn('private error',str(result.degraded))
        self.assertEqual(retriever.queries[0].company.activity,P.activity)


    async def test_slow_native_reranker_respects_request_deadline(self):
        finished=threading.Event()
        def slow(*args,**kwargs):
            finished.wait(.5)
            return []
        case=GetBanners(FakeProfiles(P),FakeRetriever([news(1,{'factor:key_rate'})]),
                        SimpleNamespace(rerank=slow),FakeBranch(),Limits(banner_slo_s=.03))
        try:
            result=await asyncio.wait_for(case.execute(BannersCommand(P.company_id)),.25)
            self.assertFalse(result.banners)
            self.assertTrue(any('reranker' in reason for reason in result.degraded))
        finally:finished.set()

    async def test_semantic_candidate_budget_is_configurable_and_bounded(self):
        from unittest.mock import patch
        from orchestrator.config import Settings
        from orchestrator.main import create_default_app
        from orchestrator.application.use_cases import Limits
        self.assertEqual(Settings().news_candidate_limit,80)
        for bad in (0,101,True,40.5):
            with self.subTest(bad=bad),self.assertRaises(ValueError):Settings(news_candidate_limit=bad)
        with patch.dict('os.environ',{'NEWS_CANDIDATE_LIMIT':'80'}):
            self.assertEqual(Settings.from_env().news_candidate_limit,80)
        with patch('orchestrator.main.build_container') as build, patch('orchestrator.main.create_app'), patch('orchestrator.main.register_materials'):
            create_default_app(Settings(news_search_mode='local_semantic',news_candidate_limit=80))
            self.assertEqual(build.call_args.args[4].retrieve_limit,80)
            create_default_app(Settings(news_search_mode='lexical',news_candidate_limit=80))
            self.assertEqual(build.call_args.args[4].retrieve_limit,Limits().retrieve_limit)

    async def test_invalid_backend_fails_before_loading_models(self):
        from orchestrator.main import create_default_app
        from orchestrator.config import Settings
        with self.assertRaisesRegex(ValueError,'NEWS_SEARCH_MODE'):
            create_default_app(Settings(news_search_mode='typo'))
