import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch, AsyncMock

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from ingestion import database
from orchestrator.domain.models import NewsQuery
from orchestrator.api.materials import register_materials, registered_pdf
from retrieval.news import PostgresNewsRetriever

AS_OF = datetime(2026,9,14,12,tzinfo=timezone.utc)


class BoundariesTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_input_and_unknown_profile_do_not_connect(self):
        r=PostgresNewsRetriever()
        with patch('retrieval.news.open_connection',new_callable=AsyncMock) as conn:
            for query in [NewsQuery(frozenset(),limit=101),NewsQuery(frozenset(),question=' '),
                          NewsQuery(frozenset(),as_of=datetime(2026,1,1)),
                          NewsQuery(frozenset(),question='x'*2001)]:
                with self.assertRaises(ValueError):await r.retrieve(query)
            self.assertEqual((await r.retrieve(NewsQuery(frozenset({'unknown:x'})))).news,())
            conn.assert_not_called()

    async def test_connection_timeout_and_cancellation_are_bounded(self):
        async def slow():await asyncio.sleep(10)
        with patch('retrieval.news.open_connection',side_effect=slow):
            with self.assertRaises(TimeoutError):
                await PostgresNewsRetriever(timeout_s=0.02).retrieve(NewsQuery(frozenset({'factor:key_rate'})))


class MaterialTests(unittest.TestCase):
    def test_local_runner_never_inherits_an_external_database(self):
        from scripts.local import environment
        with patch.dict(os.environ,{'DATABASE_URL':'external', 'RETRIEVAL_DATABASE_URL':'external'}), \
                patch('scripts.local.pg_bin',return_value=Path('/test/bin')):
            env=environment()
        self.assertNotIn('DATABASE_URL',env)
        self.assertNotIn('RETRIEVAL_DATABASE_URL',env)
        self.assertEqual(env['PGDATABASE'],'insights')


    def test_filesystem_escape_and_non_pdf_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'reports';root.mkdir()
            private=Path(tmp)/'private.pdf';private.write_bytes(b'private')
            (root/'public.pdf').write_bytes(b'%PDF')
            (root/'link.pdf').symlink_to(private)
            for filename in ['../private.pdf','link.pdf','missing.pdf','../.env']:
                with self.subTest(filename=filename),self.assertRaises(HTTPException):registered_pdf(root,filename)
            self.assertEqual(registered_pdf(root,'public.pdf'),(root/'public.pdf').resolve())

    def test_api_does_not_leak_database_error(self):
        app=FastAPI();register_materials(app,'.')
        with TestClient(app) as client:
            self.assertEqual(client.get('/v1/materials/not-a-document').status_code,404)
            with patch('orchestrator.api.materials.open_connection',new=AsyncMock(side_effect=RuntimeError('password=secret'))):
                reply=client.get('/v1/materials/'+'a'*64)
                self.assertEqual(reply.status_code,503)
                self.assertNotIn('secret',reply.text)


@unittest.skipUnless(os.environ.get('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class NewsRetrievalTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin=psycopg.connect(os.environ.get('DATABASE_URL',''),autocommit=True)
        cls.name='insights_news_search_'+uuid.uuid4().hex
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.connect_patch=patch('ingestion.database.connect',side_effect=lambda:psycopg.connect(cls.admin.info.dsn,dbname=cls.name))
        cls.connect_patch.start();database.initialize()
        async def connect_async():
            return await psycopg.AsyncConnection.connect(cls.admin.info.dsn,dbname=cls.name,row_factory=dict_row)
        cls.async_patch=patch('retrieval.news.open_connection',new=connect_async)
        cls.async_patch.start()
        with database.connect() as c:
            c.execute("INSERT INTO insights.news_imports VALUES (%s,'test',6,'{}','{}',now())",('a'*64,))
            for num,title,stamp in [
                (1,'Ключевая ставка снижена','2026-09-13'),
                (2,'Продажи магазинов выросли','2026-09-13'),
                (3,'Ключевая ставка в будущем','2026-09-15'),
                (4,'Ключевая ставка старый отчёт','2025-01-01'),
                (5,'Ключевая ставка снижена','2026-09-13'),
                (6,'Курс юаня вырос','2026-09-12')]:
                c.execute('''INSERT INTO insights.news_articles
                    (news_id,url,title,text,source,source_kind,published_at,fetched_at,text_sha256,
                     source_record,raw_receipt,first_import_sha256)
                    VALUES (%s,%s,%s,%s,'test','media',%s,now(),%s,'{}','{}',%s)''',
                    (f'n{num}',f'https://example.org/{num}',title,(title+' ')*30,stamp+'T10:00:00Z',f'{num:064x}','a'*64))
            for num,stamp,review in [(1,'2026-09-12',False),(2,'2026-09-15',False),(3,'2026-09-12',True)]:
                doc=f'{num:064x}'
                c.execute('''INSERT INTO insights.documents
                    (document_id,source_file,title,sha256,published_at,page_count,extraction_settings,artifact_sha256)
                    VALUES (%s,%s,'Ключевая ставка',%s,%s,1,'{}',%s)''',(doc,f'{num}.pdf',doc,stamp,doc))
                c.execute("INSERT INTO insights.document_pages VALUES (%s,1,'Ключевая ставка снижена','native',100,%s)",(doc,review))
                c.execute("INSERT INTO insights.document_chunks (chunk_id,document_id,page_number,chunk_index,start_char,end_char,text) VALUES (%s,%s,1,0,0,22,'Ключевая ставка снижена')",(doc,doc))

    @classmethod
    def tearDownClass(cls):
        cls.async_patch.stop();cls.connect_patch.stop()
        cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)));cls.admin.close()

    async def test_profile_dates_duplicate_headlines_and_bank_provenance(self):
        r=PostgresNewsRetriever(timeout_s=2)
        result=await r.retrieve(NewsQuery(frozenset({'factor:key_rate'}),as_of=AS_OF))
        self.assertEqual(len(result.news),1)
        self.assertIn(result.news[0].news_id,('n1','n5'))
        self.assertIn('factor:key_rate',result.news[0].tags)
        self.assertEqual(len(result.bank_materials),1)
        self.assertEqual(result.bank_materials[0].doc_id,'bank:'+f'{1:064x}')
        self.assertEqual(result.bank_materials[0].url,'/v1/materials/'+f'{1:064x}'+'#page=1')
        trade=await r.retrieve(NewsQuery(frozenset({'sector:trade'}),as_of=AS_OF))
        self.assertEqual([n.news_id for n in trade.news],['n2'])

    async def test_chat_requires_question_match_and_sql_input_is_data(self):
        r=PostgresNewsRetriever(timeout_s=2)
        query=NewsQuery(frozenset({'factor:key_rate'}),question='курс юаня',as_of=AS_OF)
        self.assertEqual([n.news_id for n in (await r.retrieve(query)).news],['n6'])
        for text in ['доходы марсианских колоний',"'; DROP TABLE insights.news_articles; --"]:
            reply=await r.retrieve(NewsQuery(frozenset({'factor:key_rate'}),question=text,as_of=AS_OF))
            self.assertEqual(reply.news,())
        with database.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM insights.news_articles').fetchone()[0],6)

    async def test_unavailable_pdf_is_not_returned_as_a_broken_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=await PostgresNewsRetriever(timeout_s=2,reports_root=tmp).retrieve(
                NewsQuery(frozenset({'factor:key_rate'}),as_of=AS_OF))
            self.assertTrue(result.news)
            self.assertEqual(result.bank_materials,())

    async def test_geography_is_not_a_russian_word_stem(self):
        with database.connect() as c:
            for num,title in [(7,'В Дании открыли новый порт'),(8,'По данным статистики открыли новый порт')]:
                c.execute("""INSERT INTO insights.news_articles
                    (news_id,url,title,text,source,source_kind,published_at,fetched_at,text_sha256,
                     source_record,raw_receipt,first_import_sha256)
                    SELECT %s,%s,%s,%s,source,source_kind,published_at,fetched_at,%s,
                           source_record,raw_receipt,first_import_sha256
                    FROM insights.news_articles WHERE news_id='n1'""",
                    (f'n{num}',f'https://example.org/{num}',title,title*30,f'{num:064x}'))
        try:
            retriever=PostgresNewsRetriever(timeout_s=2,bank_enabled=False)
            geo=await retriever.retrieve(NewsQuery(frozenset({'country:ДАНИЯ'}),as_of=AS_OF))
            self.assertEqual([n.news_id for n in geo.news],['n7'])
            chat=await retriever.retrieve(NewsQuery(frozenset({'country:ДАНИЯ'}),question='данным статистики',as_of=AS_OF))
            self.assertEqual([n.news_id for n in chat.news],['n8'])
            self.assertNotIn('country:ДАНИЯ',chat.news[0].tags)
        finally:
            with database.connect() as c:c.execute("DELETE FROM insights.news_articles WHERE news_id IN ('n7','n8')")

    async def test_database_enforces_read_only_transaction(self):
        with patch('retrieval.news.NEWS_SQL','DELETE FROM insights.news_articles'):
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                await PostgresNewsRetriever(timeout_s=2).retrieve(NewsQuery(frozenset({'factor:key_rate'}),as_of=AS_OF))
        with database.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM insights.news_articles').fetchone()[0],6)


if __name__=='__main__':unittest.main()
