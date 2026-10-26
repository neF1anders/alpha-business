import csv
import tempfile
import unittest
from pathlib import Path

from orchestrator.domain.models import CompanyProfile, SourceKind
from orchestrator.domain.profile_rules import features_of, segment_of
from orchestrator.domain.ranking import TagReranker
from orchestrator.infrastructure.profiles_csv import CsvProfileRepository
from tests.orchestrator_support import NOW, PROFILE, news


class RerankerTests(unittest.TestCase):
    def setUp(self):
        self.reranker = TagReranker()

    def rank(self, items, profile=PROFILE, limit=5, **kw):
        return self.reranker.rerank(profile, items, limit, now=NOW, **kw)

    def test_matching_news_outranks_unrelated_and_unrelated_is_dropped(self):
        result = self.rank([news(1, {"factor:fx"}), news(2, {"factor:key_rate"}), news(3, {"sector:trade"})])
        self.assertEqual([r.item.news_id for r in result], ["n2", "n3"])
        self.assertEqual(result[0].matched_tags, ("factor:key_rate",))

    def test_product_modifier_boosts_key_rate_news(self):
        with_credit = self.rank([news(1, {"factor:key_rate"})])[0].score
        no_credit = CompanyProfile("U_2", "trade-mid", PROFILE.tags - {"product:credit"})
        self.assertGreater(with_credit, self.rank([news(1, {"factor:key_rate"})], no_credit)[0].score)

    def test_role_lens_reorders_only_with_explicit_role(self):
        no_credit = CompanyProfile("U_2", "trade-mid", PROFILE.tags - {"product:credit"})
        items = [news(1, {"factor:key_rate"}), news(2, {"sector:trade"})]
        self.assertEqual(self.rank(items, no_credit)[0].item.news_id, "n1")
        sales = self.rank(items, no_credit.with_role("sales"))
        self.assertEqual(sales[0].item.news_id, "n2")
        self.assertIn("role_focus=sales:sector:trade", sales[0].reasons)

    def test_explicit_role_gets_its_own_audience(self):
        self.assertEqual(PROFILE.audience_id, PROFILE.company_id)
        self.assertEqual(PROFILE.with_role("ceo").audience_id, f"{PROFILE.company_id}~ceo")

    def test_fresh_and_primary_source_win_ties(self):
        old = news(1, {"factor:key_rate"}, days=30)
        fresh = news(2, {"factor:key_rate"}, days=0)
        primary = news(3, {"factor:key_rate"}, days=0, kind=SourceKind.PRIMARY)
        self.assertEqual([r.item.news_id for r in self.rank([old, fresh, primary])], ["n3", "n2", "n1"])

    def test_one_news_per_cluster_and_limit(self):
        items = [news(1, {"factor:key_rate"}, cluster="x", score=0.1), news(2, {"factor:key_rate"}, cluster="x", score=0.9),
                 news(3, {"sector:trade"}), news(4, {"factor:consumer_demand"})]
        self.assertEqual(len(self.rank(items)), 3)
        self.assertEqual(self.rank(items, limit=1)[0].item.news_id, "n2")

    def test_chat_mode_keeps_news_without_tag_match(self):
        items = [news(1, {"factor:fx"}, score=0.8)]
        self.assertEqual(self.rank(items), [])
        self.assertEqual(len(self.rank(items, require_tag_match=False)), 1)

    def test_scores_stay_in_unit_interval(self):
        for r in self.rank([news(i, {"factor:key_rate", "sector:trade", "okved:47"}, days=0, kind=SourceKind.PRIMARY,
                                 score=1.0) for i in range(3)]):
            self.assertLessEqual(r.score, 1.0)


class ProfileRulesTests(unittest.TestCase):
    def test_segment_is_sector_and_size(self):
        self.assertEqual(segment_of({"sector:trade", "size:mid", "region:Москва"}), "trade-mid")
        self.assertEqual(segment_of({"region:Москва"}), "unknown-unknown")

    def test_features_only_from_confirmed_tags(self):
        feats = features_of({"sector:trade", "product:credit", "product:rko", "factor:fx"})
        self.assertIn("отрасль: торговля", feats)
        self.assertIn("продукты банка: кредитные продукты", feats)
        self.assertIn("есть внешнеторговые операции", feats)
        self.assertEqual(features_of(set()), ())


class CsvProfileRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def write(self, tmp, rows):
        path = Path(tmp) / "company_tags.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["synthetic_u", "tag", "namespace"])
            w.writerows(rows)
        return path

    async def test_builds_profile_from_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = CsvProfileRepository(self.write(tmp, [("U_1", "sector:trade", "sector"), ("U_1", "size:mid", "size"),
                                                         ("U_2", "sector:agro", "sector")]))
            profile = await repo.get("U_1")
            self.assertEqual(profile.segment_id, "trade-mid")
            self.assertEqual(profile.tags, frozenset({"sector:trade", "size:mid"}))
            self.assertIsNone(await repo.get("U_404"))

    async def test_missing_file_has_actionable_error(self):
        with self.assertRaisesRegex(FileNotFoundError, "build_tags"):
            await CsvProfileRepository("no/such/file.csv").get("U_1")


if __name__ == "__main__":
    unittest.main()
