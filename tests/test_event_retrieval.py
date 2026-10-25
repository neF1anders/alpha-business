import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import AsyncMock, patch

import psycopg
from orchestrator.domain.models import CompanyProfile, NewsItem
from retrieval.events import PostgresEventRetriever

NOW=datetime(2026,9,14,12,tzinfo=timezone.utc)
PROFILE=CompanyProfile('synthetic','synthetic',frozenset({'product:credit','factor:key_rate'}))
def event(title='Ключевая ставка сохранена'):
    return NewsItem('unseen-external-event','event',title,title+' Подробности события.',
                    'https://example.org/new',NOW-timedelta(days=1))


class EventBoundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_future_event_and_invalid_bounds_never_connect(self):
        with patch('retrieval.events.open_connection',new_callable=AsyncMock) as c:
            for item,limit in [(replace(event(),published_at=NOW+timedelta(days=1)),5),
                               (event(),0),(replace(event(),text=''),5)]:
                with self.assertRaises(ValueError):
                    await PostgresEventRetriever().retrieve(item,PROFILE,as_of=NOW,limit=limit)
            c.assert_not_called()

    async def test_timeout_is_bounded(self):
        async def slow(): await asyncio.sleep(5)
        with patch('retrieval.events.open_connection',side_effect=slow):
            with self.assertRaises(TimeoutError):
                await PostgresEventRetriever(timeout_s=.01).retrieve(event(),PROFILE,as_of=NOW)


@unittest.skipUnless(os.environ.get('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class EventDatabaseTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the isolated database fixture: dated/future/OCR bank chunks.
        from tests.test_news_retrieval import NewsRetrievalTests
        cls.fixture=NewsRetrievalTests
        cls.fixture.setUpClass()
        from retrieval import news
        cls.connection=patch('retrieval.events.open_connection',new=news.open_connection)
        cls.connection.start()

    @classmethod
    def tearDownClass(cls):
        cls.connection.stop();cls.fixture.tearDownClass()

    async def test_unseen_event_provenance_and_different_profiles(self):
        r=PostgresEventRetriever(timeout_s=2)
        borrower=await r.retrieve(event(),PROFILE,as_of=NOW)
        control=await r.retrieve(event(),replace(PROFILE,company_id='control',tags=frozenset()),as_of=NOW)
        self.assertEqual(borrower.topics,('policy_rate',))
        self.assertGreater(borrower.heuristic_score,control.heuristic_score)
        self.assertEqual(borrower.trace['profile_hints'],['кредит'])
        self.assertEqual(control.trace['profile_hints'],[])
        self.assertEqual(len(borrower.bank_materials),1)
        material=borrower.bank_materials[0]
        self.assertEqual(material.news_ids,('unseen-external-event',))
        self.assertEqual(material.published_at.isoformat(),'2026-09-12')
        self.assertTrue(material.doc_id.startswith('bank:'))
        self.assertEqual(material.page,1)
        self.assertEqual(borrower.trace['evidence_role'],'dated_background_not_current_bank_verdict')
        from ingestion.database import connect
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM insights.news_articles WHERE news_id='unseen-external-event'").fetchone()[0],0)

    async def test_no_bank_match_is_explicit_and_profile_cannot_invent_event_topic(self):
        r=PostgresEventRetriever(timeout_s=2)
        unknown=await r.retrieve(event('Квантовая телепортация'),PROFILE,as_of=NOW)
        self.assertEqual(unknown.status,'no_supported_topic')
        self.assertEqual(unknown.bank_materials,())
        fishing=await r.retrieve(event('Вылов лосося снизился'),PROFILE,as_of=NOW)
        self.assertEqual(fishing.topics,('fish',))
        self.assertEqual(fishing.status,'no_eligible_bank_context')
        self.assertEqual(fishing.bank_materials,())
        # Even if lead mentions interest rates, a fishing headline anchors fishing.
        lead=replace(event('Вылов лосося снизился'),text='Ключевая ставка повышена.')
        self.assertEqual((await r.retrieve(lead,PROFILE,as_of=NOW)).bank_materials,())

    async def test_event_geography_does_not_match_data(self):
        p=replace(PROFILE,tags=PROFILE.tags | {'country:ДАНИЯ'})
        r=PostgresEventRetriever(timeout_s=2)
        result=await r.retrieve(replace(event(),text='По данным регулятора ставка сохранена.'),p,as_of=NOW)
        self.assertNotIn('country:ДАНИЯ',result.event_tags)
        actual=await r.retrieve(event('Ключевая ставка в Дании сохранена'),p,as_of=NOW)
        self.assertIn('country:ДАНИЯ',actual.event_tags)

    async def test_read_only_and_earlier_cutoff(self):
        with patch('retrieval.events.EVENT_BANK_SQL','DELETE FROM insights.news_articles'):
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                await PostgresEventRetriever(timeout_s=2).retrieve(event(),PROFILE,as_of=NOW)
        early=NOW-timedelta(days=4)
        result=await PostgresEventRetriever(timeout_s=2).retrieve(replace(event(),published_at=early),PROFILE,as_of=early)
        self.assertEqual(result.bank_materials,())
