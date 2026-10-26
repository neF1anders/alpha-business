"""Досье компании, перевод в модели ветки и деградация баннера по сроку."""
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from orchestrator.application.use_cases import BannersCommand, GetBanners, Limits
from orchestrator.domain.models import CompanyDossier, CounterpartySector, raw_banner_id
from orchestrator.domain.profile_rules import counterparty_search_tags, sector_of_division
from orchestrator.domain.ranking import TagReranker
from orchestrator.infrastructure.postgres_dossier import build_dossier
from orchestrator.infrastructure.summary_branch_adapter import news_tags, to_company_context
from orchestrator_support import PROFILE, FakeBranch, FakeProfiles, FakeRetriever, news

ROW = {"mainokved_code": "46.72", "mainokved_name": "Торговля оптовая металлами", "region": "Москва", "city": "г. Москва",
       "cl_type": "CLIENT", "fts_flag": True, "fts_country_name": "КИТАЙ, ТУРЦИЯ", "revenue_company": 1000,
       "amount_workers_fns_gradation": "16-100", "eco_mark_label": "Оптимизация", "company_age_years_gradation": "8-15",
       "negativemark": False, "group_id": "g1", "cl_product_rko_mark": True, "cl_product_loa_mark": True,
       "factoring_mark": False}
FLOWS = [
    {"side": "supplier", "division": "24", "name": "Производство чугуна, стали и ферросплавов", "amount": 600,
     "counterparties": 2, "foreign_amount": 0, "side_total": 1000, "side_top": 500},
    {"side": "supplier", "division": None, "name": None, "amount": 400, "counterparties": 7, "foreign_amount": 0,
     "side_total": 1000, "side_top": 500},                                   # контрагенты без профиля
    {"side": "buyer", "division": "41", "name": "Строительство жилых зданий", "amount": 300, "counterparties": 4,
     "foreign_amount": 150, "side_total": 300, "side_top": 120},
]


class DossierTests(unittest.TestCase):
    def test_build_dossier_keeps_only_sectors_and_shares(self):
        d = build_dossier(ROW, [{"division": "68", "companies": 3}], FLOWS)
        self.assertEqual(d.products, frozenset({"rko", "loan"}))
        self.assertEqual(d.trade_countries, ("КИТАЙ", "ТУРЦИЯ"))
        self.assertIsNone(d.revenue)                                          # заглушка 1000 — пропуск
        self.assertEqual([(s.division, s.share) for s in d.suppliers], [("24", 0.6)])   # без профиля — только в сумме
        self.assertEqual((d.top_supplier_share, d.buyers[0].foreign_trade_share), (0.5, 0.5))
        self.assertEqual(d.affiliate_sectors, ("68",))

    def test_counterparty_search_tags_use_key_partner_sectors(self):
        d = CompanyDossier(suppliers=(CounterpartySector("24", "", 0.6, 1), CounterpartySector("10", "", 0.05, 1)),
                           buyers=(CounterpartySector("41", "", 0.3, 1),))
        self.assertEqual(counterparty_search_tags(d), {"sector:manufacturing", "sector:construction"})
        self.assertIsNone(sector_of_division("ab"))

    def test_company_context_from_dossier_and_from_tags(self):
        d = build_dossier(ROW, [], FLOWS)
        ctx = to_company_context(PROFILE, d)
        self.assertEqual(ctx.profile.payments.suppliers[0].division, "24")
        self.assertEqual(ctx.profile.relationship.value, "client")
        from_tags = to_company_context(PROFILE, None).profile
        self.assertEqual((from_tags.okved_code, from_tags.region), ("47", "Москва"))
        self.assertEqual({p.value for p in from_tags.products}, {"loan"})

    def test_news_tags_add_branch_topics_and_okved_divisions(self):
        tags = news_tags(frozenset({"factor:key_rate", "sector:transport"}))
        self.assertTrue({"rate", "okved:49", "okved:53", "factor:key_rate"} <= set(tags))


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_news_comes_as_raw_banner_with_cluster_id(self):
        now = datetime.now(timezone.utc)
        items = [replace(news(1, {"factor:key_rate"}, cluster="c-rate"), published_at=now)]
        service = GetBanners(FakeProfiles(PROFILE), FakeRetriever(items), TagReranker(), FakeBranch(delay=5),
                             Limits(banner_slo_s=0.2))
        result = await service.execute(BannersCommand("U_1"))
        self.assertEqual([b.banner_id for b in result.banners], [raw_banner_id("c-rate")])
        self.assertEqual(result.banners[0].cluster_id, "c-rate")
        self.assertTrue(result.degraded)


if __name__ == "__main__":
    unittest.main()
