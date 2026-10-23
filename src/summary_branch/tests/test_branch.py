"""Сквозные тесты ветки: критерии 2, 3, 5, 9, 10, 11."""
import logging

import pytest
from factories import make_company, make_news, make_req
from summary_branch import interfaces as ifc
from summary_branch.bootstrap import build_branch
from summary_branch.config import BranchConfig
from summary_branch.fakes import FakeLLMClient, StaticDirectory
from summary_branch.models import BudgetTier, Decision, Mode

SEGMENT = "metal-large-north"
COMPANIES = [make_company("co1"), make_company("co2", features=["экспорт в ЕАЭС"]),
             make_company("co3", segment_id="other-segment")]


def runtime(llm=None, **kw):
    llm = llm or FakeLLMClient()
    return llm, build_branch(llm, StaticDirectory(COMPANIES), **kw)


def banner_ids(rt, company_id):
    return sorted(d.banner_id for d in rt.store.top_k(company_id, 10))


def test_components_implement_protocols():
    llm, rt = runtime()
    assert isinstance(rt.branch, ifc.SummaryBranch)
    assert isinstance(rt.store, ifc.BannerStore) and isinstance(rt.cache, ifc.SegmentCache)
    assert isinstance(llm, ifc.LLMClient)
    assert isinstance(StaticDirectory([]), ifc.CompanyDirectory)


def test_llm_called_once_per_cluster_and_zero_on_repeat():
    llm, rt = runtime()
    req = make_req()
    banners = rt.branch.handle_banner(req)
    assert {b.company_id for b in banners} == {"co1", "co2"}       # co3 из другого сегмента
    assert llm.call_count == 1                                      # две компании — один вызов
    rt.branch.handle_banner(req)
    assert llm.call_count == 1                                      # повтор целиком из кэша
    assert rt.cache.hits >= 1


def test_rerun_does_not_duplicate_banners():
    _, rt = runtime()
    req = make_req()
    first = {b.banner_id for b in rt.branch.handle_banner(req)}
    second = {b.banner_id for b in rt.branch.handle_banner(req)}
    assert first == second
    assert len(banner_ids(rt, "co1")) == 1 and len(banner_ids(rt, "co2")) == 1


def test_for_you_is_personalized_per_company():
    _, rt = runtime()
    rt.branch.handle_banner(make_req())
    assert "3 кредитных продукта" in rt.store.top_k("co1", 1)[0].for_you
    assert rt.store.top_k("co2", 1)[0].for_you == "Учтите это при планировании"   # нет подходящего признака


def test_only_top_clusters_are_summarized():
    news = [make_news(f"n{i}", f"c{i}", rerank_score=0.5 + i / 20) for i in range(5)]
    llm, rt = runtime()
    banners = rt.branch.handle_banner(make_req(news=news))
    assert llm.call_count == BranchConfig().max_clusters_per_request == 3
    assert {b.key.cluster_id for b in banners} == {"c4", "c3", "c2"}


def test_cached_only_without_cache_hit_makes_no_call_and_does_not_raise():
    llm, rt = runtime()
    assert rt.branch.handle_banner(make_req(budget=BudgetTier.CACHED_ONLY)) == []
    assert llm.call_count == 0


def test_cached_only_uses_warm_cache():
    llm, rt = runtime()
    rt.branch.handle_banner(make_req())
    assert rt.branch.handle_banner(make_req(budget=BudgetTier.CACHED_ONLY))
    assert llm.call_count == 1


def test_broken_json_retry_then_skip_without_exception(caplog):
    llm, rt = runtime(FakeLLMClient(broken_responses=1))
    assert rt.branch.handle_banner(make_req())                      # один повтор спасает
    assert llm.call_count == 2

    llm2, rt2 = runtime(FakeLLMClient(broken_responses=9))
    with caplog.at_level(logging.WARNING):
        assert rt2.branch.handle_banner(make_req()) == []
    assert llm2.call_count == 2
    assert "кластер пропущен" in caplog.text and "t1" in caplog.text


def test_fabricated_number_never_gets_show_summary():
    def fabricate(draft):
        draft["claims"][0]["text"] = "Банк России повысил ставку до 16%"
        return draft
    _, rt = runtime(FakeLLMClient(tamper=fabricate))
    banners = rt.branch.handle_banner(make_req())
    assert banners and all(b.decision != Decision.SHOW_SUMMARY and b.confidence <= 0.5 for b in banners)


def test_cluster_without_supported_facts_creates_no_banner():
    def strip_evidence(draft):
        for claim in draft["claims"]:
            claim["evidence_ids"] = ["ghost"]
        return draft
    _, rt = runtime(FakeLLMClient(tamper=strip_evidence))
    assert rt.branch.handle_banner(make_req()) == []


def test_urgent_banner_is_pushed_only_for_attention_with_high_score():
    pushed = []
    _, rt = runtime(push_handler=lambda cid, dtos: pushed.append(cid))
    rt.branch.handle_banner(make_req())
    assert sorted(pushed) == ["co1", "co2"]

    pushed.clear()
    rt.branch.handle_banner(make_req())                              # повторный прогон не пушит второй раз
    assert pushed == []

    _, rt_low = runtime(push_handler=lambda cid, dtos: pushed.append(cid))
    rt_low.branch.handle_banner(make_req(news=[make_news(rerank_score=0.2)],
                                         company=make_company(tags=["agro"])))
    assert pushed == []


def test_top_banners_respects_top_k_serve_and_injected_store():
    from summary_branch.stores import InMemoryBannerStore, InMemorySegmentCache
    store, cache = InMemoryBannerStore(), InMemorySegmentCache()
    news = [make_news(f"n{i}", f"c{i}", rerank_score=0.9) for i in range(3)]
    rt = build_branch(FakeLLMClient(), StaticDirectory(COMPANIES), BranchConfig(top_k_serve=2, max_clusters_per_request=3),
                      store=store, cache=cache)
    rt.branch.handle_banner(make_req(news=news))
    assert rt.store is store and rt.cache is cache
    assert len(rt.top_banners("co1")) == 2


def test_invalidate_cluster_after_publication():
    _, rt = runtime()
    rt.branch.handle_banner(make_req())
    assert rt.store.invalidate_cluster("c1", "новое событие") == 2
    assert rt.store.top_k("co1", 3) == []


# --------------------------------------------------------------------------- чат
def chat_req(**kw):
    return make_req(mode=Mode.CHAT, question="что со ставкой?", **kw)


def test_chat_answer_is_not_written_to_banner_store():
    llm, rt = runtime()
    answer = rt.branch.handle_chat(chat_req())
    assert not answer.raw_news and answer.claims and answer.sources and answer.follow_ups
    assert rt.store.top_k("co1", 10) == []
    assert rt.cache.hits == 0 and rt.cache.misses == 0


def test_chat_low_confidence_returns_raw_news():
    _, rt = runtime()
    answer = rt.branch.handle_chat(chat_req(news=[make_news(rerank_score=0.1)], company=make_company(tags=["agro"])))
    assert answer.raw_news and answer.claims == [] and answer.sources[0].url == "https://cbr.ru/x"


def test_chat_medium_confidence_sets_caveat():
    _, rt = runtime()
    answer = rt.branch.handle_chat(chat_req(news=[make_news(rerank_score=0.5)], company=make_company(tags=["agro"])))
    assert answer.caveat and not answer.raw_news and answer.claims


@pytest.mark.parametrize("llm_kwargs,budget", [({"broken_responses": 9}, BudgetTier.FULL),
                                               ({}, BudgetTier.CACHED_ONLY)])
def test_chat_falls_back_to_raw_news_on_model_errors(llm_kwargs, budget):
    llm, rt = runtime(FakeLLMClient(**llm_kwargs))
    answer = rt.branch.handle_chat(chat_req(budget=budget))
    assert answer.raw_news and answer.claims == [] and answer.sources
    if budget == BudgetTier.CACHED_ONLY:
        assert llm.call_count == 0
