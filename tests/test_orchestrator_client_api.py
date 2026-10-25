"""Вход фронта `/api/companies/...` поверх настоящей ветки саммари с детерминированным LLM."""
import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.api.client import sse_frame
from orchestrator.application.use_cases import Limits
from orchestrator.domain.models import CompanyDossier, CounterpartySector
from orchestrator.domain.ranking import TagReranker
from orchestrator.infrastructure.signal_hub import SignalHub
from orchestrator.infrastructure.summary_branch_adapter import (
    BranchBannerFeed, SummaryBranchAdapter, banner_from_dto,
)
from orchestrator.main import build_container
from orchestrator_support import PROFILE, FakeProfiles, FakeRetriever, news
from summary_branch import build_branch
from summary_branch.infrastructure.fakes import FakeLLMClient

NOW = datetime.now(timezone.utc)
STEEL = replace(news(1, {"sector:manufacturing", "factor:commodities"}, cluster="steel"),
                title="Металлурги подняли цены на прокат на 12%", published_at=NOW,
                text="Крупнейшие металлурги подняли цены на прокат на 12%. Причина — рост издержек.")
RATE = replace(news(2, {"factor:key_rate"}, cluster="rate"), title="Банк России сохранил ставку 14%",
               text="Совет директоров Банка России сохранил ключевую ставку на уровне 14%.", published_at=NOW)
DOSSIER = CompanyDossier(okved_code="47.11", okved_name="Розничная торговля", region="Москва", client_type="client",
                         products=frozenset({"loan"}),
                         suppliers=(CounterpartySector("24", "металлургия", 0.4, 3),), top_supplier_share=0.3)


class FakeDossiers:
    def __init__(self, dossier=DOSSIER):
        self.dossier = dossier

    async def get(self, company_id):
        return self.dossier if company_id == PROFILE.company_id else None


def make_client(retriever=None, demo_company_id=""):
    hub = SignalHub()
    runtime = build_branch(FakeLLMClient(), push_handler=lambda cid, dtos: hub.publish_banners(
        cid, [banner_from_dto(d) for d in dtos]))
    container = build_container(FakeProfiles(PROFILE), retriever or FakeRetriever([STEEL, RATE]), TagReranker(),
                                SummaryBranchAdapter(runtime), Limits(banner_slo_s=5), dossiers=FakeDossiers(),
                                feed=BranchBannerFeed(runtime), stream=hub, demo_company_id=demo_company_id)
    return TestClient(create_app(container, ("http://127.0.0.1:5173",))), hub, runtime


class ClientApiTests(unittest.TestCase):
    def setUp(self):
        self.client, self.hub, self.runtime = make_client()

    def signals(self, company="U_1"):
        r = self.client.get(f"/api/companies/{company}/signals")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_signals_are_personalized_through_supplier_path(self):
        body = self.signals()
        by_cluster = {item["cluster_id"]: item for item in body["items"]}
        steel = by_cluster["steel"]
        self.assertIn("металлургия", steel["for_you"])
        self.assertIn("40%", steel["for_you"])
        self.assertEqual(steel["placements"], ["payments", "new_payment"])
        self.assertEqual(steel["inline_text"], steel["for_you"])
        self.assertEqual(steel["sources"][0]["ref_id"], "n1")
        self.assertEqual(sum(v for k, v in body["counts"].items() if k != "hidden"), len(body["items"]))

    def test_counterparty_sector_extends_news_search(self):
        retriever = FakeRetriever([STEEL, RATE])
        client, _, _ = make_client(retriever)
        client.get("/api/companies/U_1/signals")
        self.assertIn("sector:manufacturing", retriever.queries[0].tags)   # металлургия поставщиков
        self.assertNotIn("sector:manufacturing", PROFILE.tags)

    def test_dismiss_hides_banner_and_counts_it(self):
        banner_id = self.signals()["items"][0]["banner_id"]
        r = self.client.post(f"/api/companies/U_1/signals/{banner_id}/dismiss")
        self.assertEqual(r.status_code, 204)
        body = self.signals()
        self.assertNotIn(banner_id, [i["banner_id"] for i in body["items"]])
        self.assertEqual(body["counts"]["hidden"], 1)

    def test_suppress_and_seen(self):
        banner_id = self.signals()["items"][0]["banner_id"]
        self.assertEqual(self.client.post(f"/api/companies/U_1/signals/{banner_id}/seen").status_code, 204)
        r = self.client.post(f"/api/companies/U_1/signals/{banner_id}/suppress", json={"reason": "not_relevant"})
        self.assertEqual(r.status_code, 204)
        body = self.signals()
        self.assertNotIn(banner_id, [i["banner_id"] for i in body["items"]])
        self.assertEqual(body["counts"]["hidden"], 0)                    # «не про меня» — не «скрыто»

    def test_inline_picks_strong_banner_placed_on_page(self):
        self.signals()
        inline = lambda page: self.client.get(f"/api/companies/U_1/signals/inline?page={page}").json()["item"]
        self.assertEqual(inline("payments")["cluster_id"], "rate")       # кредит компании и ставка: страница платежей
        self.assertIsNone(inline("new_payment"))                         # металл — «наблюдение», плашка не нужна
        self.assertIsNone(inline("counterparties"))

    def test_chat_answer_matches_frontend_contract(self):
        r = self.client.post("/api/companies/U_1/chat", json={"question": "Что со ставкой?"})
        self.assertEqual(r.status_code, 200, r.text)
        answer = r.json()
        self.assertTrue(answer["claims"])
        claim = answer["claims"][0]
        self.assertEqual(set(claim), {"type", "text", "evidence", "grounding"})
        refs = {s["ref_id"] for s in answer["sources"]}
        self.assertTrue(any(e["ref_id"] in refs for c in answer["claims"] for e in c["evidence"]))

    def test_chat_from_banner_searches_by_its_news_title(self):
        retriever = FakeRetriever([STEEL, RATE])
        client, _, _ = make_client(retriever)
        banner = client.get("/api/companies/U_1/signals").json()["items"][0]
        client.post("/api/companies/U_1/chat", json={"question": "Что это значит?", "banner_id": banner["banner_id"]})
        self.assertEqual(retriever.queries[-1].question, banner["sources"][0]["title"])

    def test_errors_use_frontend_format(self):
        r = self.client.get("/api/companies/U_404/signals")
        self.assertEqual((r.status_code, r.json()["error"]), (404, "company_not_found"))
        r = self.client.post("/api/companies/U_1/chat", json={"question": ""})
        self.assertEqual((r.status_code, r.json()["error"]), (422, "invalid_request"))
        r = self.client.get("/api/companies/U_1/signals/inline?page=unknown")
        self.assertEqual(r.json()["error"], "invalid_request")

    def test_demo_alias_maps_to_configured_company(self):
        client, _, _ = make_client(demo_company_id="U_1")
        self.assertEqual(client.get("/api/companies/demo-company/signals").status_code, 200)

    def test_cors_allows_frontend_origin(self):
        r = self.client.options("/api/companies/U_1/signals", headers={
            "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "GET"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "http://127.0.0.1:5173")


class SignalHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_banner_replaces_raw_news_of_its_cluster(self):
        hub = SignalHub()
        subscription = hub.subscribe("U_1")
        _, _, runtime = make_client()
        dto = runtime.branch.banner(__import__("summary_branch.domain.models", fromlist=["x"]).SummaryRequest(
            trace_id="t", mode="banner", company={"company_id": "U_1"},
            news=[{"news_id": "n1", "cluster_id": "steel", "title": "t", "text": "Цена 5%.", "url": "https://x",
                   "published_at": NOW, "rerank_score": 0.9}]))
        await asyncio.to_thread(hub.publish_banners, "U_1", [banner_from_dto(dto)])   # из рабочего потока ветки
        invalidate = await asyncio.wait_for(subscription.next(), 1)
        banners = await asyncio.wait_for(subscription.next(), 1)
        self.assertEqual((invalidate.kind, invalidate.banner_ids), ("invalidate", ("raw-steel",)))
        self.assertEqual(banners.kind, "banners")
        frame = sse_frame(banners, "http://api")
        self.assertTrue(frame.startswith("event: banners\ndata: [") and frame.endswith("\n\n"))
        subscription.close()
        self.assertEqual(hub.subscribers("U_1"), 0)

    async def test_cancelled_wait_keeps_subscription_open(self):
        hub = SignalHub()
        subscription = hub.subscribe("U_1")
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(subscription.next(), 0.01)
        hub.publish_invalidate("U_1", ["b1"])
        self.assertEqual((await asyncio.wait_for(subscription.next(), 1)).banner_ids, ("b1",))
