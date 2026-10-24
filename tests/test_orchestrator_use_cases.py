import unittest
from dataclasses import replace
from datetime import datetime, timezone

from orchestrator.application.use_cases import AnswerChat, BannersCommand, ChatCommand, GetBanners, Limits
from orchestrator.domain.errors import CompanyNotFound, DependencyUnavailable
from orchestrator.domain.models import BudgetTier, Mode
from orchestrator.domain.ranking import TagReranker
from orchestrator_support import BANK, PROFILE, FakeBranch, FakeProfiles, FakeRetriever, news

NEWS = [news(1, {"factor:key_rate"}), news(2, {"sector:trade"}), news(3, {"factor:fx"})]


def fresh(items):
    """Реранкер берёт текущее время, поэтому даты новостей ставим на сейчас."""
    now = datetime.now(timezone.utc)
    return [replace(i, published_at=now) for i in items]


def build(cls, retriever=None, branch=None, limits=Limits()):
    return cls(FakeProfiles(PROFILE), retriever or FakeRetriever(fresh(NEWS), BANK), TagReranker(),
               branch or FakeBranch(), limits)


class BannersTests(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_sends_ranked_news_to_branch(self):
        branch = FakeBranch()
        result = await build(GetBanners, branch=branch).execute(BannersCommand("U_1", role="ceo"))
        self.assertEqual(len(result.banners), 2)             # n3 (fx) не относится к компании
        self.assertEqual(result.degraded, ())
        req = branch.requests[0]
        self.assertEqual((req.mode, req.company.role, req.question), (Mode.BANNER, "ceo", None))
        self.assertEqual([r.item.news_id for r in req.news], ["n1", "n2"])
        self.assertEqual([s.name for s in result.steps], ["profile", "retrieve", "rerank", "summary_branch"])

    async def test_retriever_gets_only_matchable_tags(self):
        retriever = FakeRetriever(fresh(NEWS))
        await build(GetBanners, retriever=retriever).execute(BannersCommand("U_1"))
        tags = retriever.queries[0].tags
        self.assertIn("factor:key_rate", tags)
        self.assertNotIn("product:credit", tags)

    async def test_unknown_company(self):
        with self.assertRaises(CompanyNotFound):
            await build(GetBanners).execute(BannersCommand("U_404"))

    async def test_branch_failure_degrades_to_raw_news(self):
        result = await build(GetBanners, branch=FakeBranch(error=RuntimeError("llm down"))).execute(
            BannersCommand("U_1"))
        self.assertTrue(all(b.raw_news for b in result.banners))
        self.assertEqual(len(result.banners), 2)
        self.assertTrue(result.degraded)

    async def test_branch_timeout_respects_slo(self):
        limits = Limits(banner_slo_s=0.3)
        result = await build(GetBanners, branch=FakeBranch(delay=5), limits=limits).execute(BannersCommand("U_1"))
        self.assertTrue(all(b.raw_news for b in result.banners))
        self.assertLess(sum(s.duration_ms for s in result.steps), 600)

    async def test_retriever_failure_gives_empty_degraded_result(self):
        result = await build(GetBanners, retriever=FakeRetriever(error=ConnectionError("db"))).execute(
            BannersCommand("U_1"))
        self.assertEqual(result.banners, ())
        self.assertIn("retriever", result.degraded[0])

    async def test_no_relevant_news_does_not_call_branch(self):
        branch = FakeBranch()
        result = await build(GetBanners, retriever=FakeRetriever(fresh([NEWS[2]])), branch=branch).execute(
            BannersCommand("U_1"))
        self.assertEqual((result.banners, branch.requests), ((), []))


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_passes_question_and_bank_materials(self):
        branch = FakeBranch()
        steps = []
        result = await build(AnswerChat, branch=branch).execute(ChatCommand("U_1", "Что со ставкой?"), steps.append)
        req = branch.requests[0]
        self.assertEqual((req.mode, req.question, req.bank_materials), (Mode.CHAT, "Что со ставкой?", BANK))
        self.assertEqual(req.budget, BudgetTier.FULL)
        self.assertFalse(result.answer.raw_news)
        self.assertEqual([s.name for s in steps], [s.name for s in result.steps])

    async def test_chat_keeps_news_without_tag_match(self):
        branch = FakeBranch()
        await build(AnswerChat, retriever=FakeRetriever(fresh([NEWS[2]])), branch=branch).execute(
            ChatCommand("U_1", "курс юаня"))
        self.assertEqual(len(branch.requests[0].news), 1)

    async def test_lite_budget_when_little_time_left(self):
        branch = FakeBranch()
        limits = Limits(chat_slo_s=6.0, lite_below_s=10.0)
        await build(AnswerChat, branch=branch, limits=limits).execute(ChatCommand("U_1", "вопрос"))
        self.assertEqual(branch.requests[0].budget, BudgetTier.LITE)

    async def test_branch_failure_answers_with_news(self):
        result = await build(AnswerChat, branch=FakeBranch(error=RuntimeError("x"))).execute(ChatCommand("U_1", "q"))
        self.assertTrue(result.answer.raw_news)
        self.assertEqual(result.answer.claims[0].source_urls, ("https://example.org/1",))

    async def test_retriever_failure_is_an_error_for_chat(self):
        with self.assertRaises(DependencyUnavailable):
            await build(AnswerChat, retriever=FakeRetriever(error=ConnectionError("db"))).execute(
                ChatCommand("U_1", "q"))

    async def test_empty_search_gives_empty_raw_answer(self):
        result = await build(AnswerChat, retriever=FakeRetriever([])).execute(ChatCommand("U_1", "q"))
        self.assertEqual((result.answer.claims, result.answer.raw_news), ((), True))


if __name__ == "__main__":
    unittest.main()
