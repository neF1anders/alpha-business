import os
import unittest
import numpy as np
from datetime import datetime,timezone
from unittest.mock import patch
from retrieval.semantic import SPACE_ID,SemanticNewsIndex,build_index,windows


class WindowTests(unittest.TestCase):
    def test_full_coverage_no_silent_truncation(self):
        result=list(windows(3000,440,48))
        self.assertEqual(result[0][0],0);self.assertEqual(result[-1][1],3000)
        self.assertTrue(all(b-a<=440 for a,b in result))
        self.assertTrue(all(result[i][1]-result[i+1][0]==48 for i in range(len(result)-1)))
        with self.assertRaises(ValueError):list(windows(5,40,48))


class FakeEncoder:
    device='test'
    def passages(self,title,text):return [([1,2],0,10),([2,3],8,20)]
    def encode_ids(self,ids):
        out=np.zeros((len(ids),384),dtype='<f4');out[:,0]=1;return out


@unittest.skipUnless(os.environ.get('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class SemanticDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_news_retrieval import NewsRetrievalTests
        cls.fixture=NewsRetrievalTests;cls.fixture.setUpClass()
        from ingestion.database import connect
        cls.connection=patch('retrieval.semantic.connect',new=connect);cls.connection.start()
    @classmethod
    def tearDownClass(cls):cls.connection.stop();cls.fixture.tearDownClass()

    def test_idempotent_complete_index_and_source_changes_fail_closed(self):
        first=build_index(FakeEncoder());self.assertEqual(first['new_documents'],6)
        self.assertEqual(build_index(FakeEncoder())['new_documents'],0)
        index=SemanticNewsIndex();self.assertEqual(len(index.passages),12)
        query=np.zeros(384);query[0]=1
        hits=index.search(query,as_of=datetime(2026,9,14,tzinfo=timezone.utc),
                          published_after=datetime(2026,9,1,tzinfo=timezone.utc))
        self.assertEqual({r['news_id'] for r in hits},{'n1','n2','n5','n6'})
        self.assertEqual(len(hits),4) # one hit per news, not per passage
        from ingestion.database import connect
        with connect() as c:c.execute("UPDATE insights.news_articles SET title='changed' WHERE news_id='n1'")
        with self.assertRaises(ValueError):SemanticNewsIndex()
        with self.assertRaises(ValueError):build_index(FakeEncoder())
