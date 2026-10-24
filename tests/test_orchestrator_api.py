import csv
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.application.use_cases import Limits
from orchestrator.config import Settings
from orchestrator.domain.ranking import TagReranker
from orchestrator.infrastructure.profiles_csv import CsvProfileRepository
from orchestrator.infrastructure.sample_news import InMemoryNewsRetriever
from orchestrator.main import build_container, create_default_app
from orchestrator_support import FakeBranch

TAGS = ["sector:trade", "size:mid", "factor:key_rate", "factor:consumer_demand", "product:credit", "region:Москва"]


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "company_tags.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["synthetic_u", "tag", "namespace"])
            w.writerows(("U_1", t, t.split(":")[0]) for t in TAGS)
        cls.settings = Settings(tags_path=str(path))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.client = TestClient(create_default_app(self.settings))

    def test_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_banners(self):
        r = self.client.post("/v1/banners?debug=true", json={"company_id": "U_1", "user_id": "u1", "role": "cfo"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["banners"])
        self.assertTrue(body["banners"][0]["sources"][0]["url"].startswith("https://"))
        self.assertEqual([s["name"] for s in body["steps"]], ["profile", "retrieve", "rerank", "summary_branch"])
        self.assertIsNone(self.client.post("/v1/banners", json={"company_id": "U_1"}).json()["steps"])

    def test_unknown_company_and_bad_input(self):
        self.assertEqual(self.client.post("/v1/banners", json={"company_id": "U_404"}).status_code, 404)
        self.assertEqual(self.client.post("/v1/banners", json={"company_id": "bad id!"}).status_code, 422)
        self.assertEqual(self.client.post("/v1/chat", json={"company_id": "U_1", "question": ""}).status_code, 422)
        self.assertEqual(self.client.get("/v1/companies/U_404/profile").status_code, 404)

    def test_chat(self):
        r = self.client.post("/v1/chat", json={"company_id": "U_1", "question": "Что со ставкой ЦБ?"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({c["kind"] for c in r.json()["answer"]["claims"]}, {"fact", "bank", "model"})

    def test_profile_explains_segment_and_tags(self):
        body = self.client.get("/v1/companies/U_1/profile").json()
        self.assertEqual(body["segment_id"], "trade|mid")
        self.assertIn("factor:key_rate", body["tags"])

    def test_ws_banners_and_chat_with_progress(self):
        with self.client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "banners", "company_id": "U_1"})
            messages = self._until(ws, "banners")
            self.assertIn("progress", [m["type"] for m in messages])
            self.assertTrue(messages[-1]["banners"])
            ws.send_json({"type": "chat", "company_id": "U_1", "question": "Что со ставкой?"})
            self.assertEqual(self._until(ws, "answer")[-1]["answer"]["claims"][0]["kind"], "fact")

    def test_ws_errors_keep_connection_open(self):
        with self.client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "chat", "company_id": "U_1"})
            self.assertEqual(ws.receive_json()["code"], 422)
            ws.send_json({"type": "banners", "company_id": "U_404"})
            self.assertEqual(self._until(ws, "error")[-1]["code"], 404)
            ws.send_json({"type": "banners", "company_id": "U_1"})
            self.assertEqual(self._until(ws, "banners")[-1]["type"], "banners")

    def test_branch_failure_still_returns_banners(self):
        container = build_container(CsvProfileRepository(self.settings.tags_path), InMemoryNewsRetriever(),
                                    TagReranker(), FakeBranch(error=RuntimeError("x")), Limits())
        body = TestClient(create_app(container)).post("/v1/banners", json={"company_id": "U_1"}).json()
        self.assertTrue(all(b["raw_news"] for b in body["banners"]))
        self.assertTrue(body["degraded"])

    @staticmethod
    def _until(ws, kind):
        out = []
        while True:
            msg = ws.receive_json()
            out.append(msg)
            if msg["type"] == kind:
                return out


if __name__ == "__main__":
    unittest.main()
