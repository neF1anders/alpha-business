"""Сквозные тесты ветки: критерии 2, 3, 5, 9, 10, 11."""
import logging

import pytest
from factories import make_company, make_news, make_req
from summary_branch.domain import ports as ifc
from summary_branch.bootstrap import build_branch
from summary_branch.config import BranchConfig
from summary_branch.infrastructure.fakes import FakeLLMClient, StaticDirectory
from summary_branch.domain.models import BudgetTier, Decision, Mode

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
    from summary_branch.infrastructure.stores import InMemoryBannerStore, InMemorySegmentCache
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


def test_runtime_invalidate_cluster_forces_resummarization_and_new_push():
    pushed = []
    llm, rt = runtime(push_handler=lambda cid, dtos: pushed.append(cid))
    rt.branch.handle_banner(make_req())
    assert llm.call_count == 1 and len(pushed) == 2
    assert rt.invalidate_cluster("c1", "новое событие") == 2
    rt.branch.handle_banner(make_req())
    assert llm.call_count == 2                       # саммари пересобрано, а не взято из кэша
    assert len(pushed) == 4                          # заменённый баннер считается новым
    assert len(rt.top_banners("co1")) == 1


def test_clusters_are_summarized_in_parallel_and_order_is_kept():
    import threading
    barrier = threading.Barrier(3, timeout=3)      # пройдёт, только если три вызова LLM идут одновременно

    class WaitingLLM(FakeLLMClient):
        def generate_json(self, prompt, schema, *, tier, trace_id):
            barrier.wait()
            return super().generate_json(prompt, schema, tier=tier, trace_id=trace_id)

    news = [make_news(f"n{i}", f"c{i}", rerank_score=0.9 - i / 10) for i in range(3)]
    llm = WaitingLLM()
    rt = build_branch(llm, StaticDirectory(COMPANIES))
    banners = rt.branch.handle_banner(make_req(news=news))
    assert llm.call_count == 3
    assert [b.key.cluster_id for b in banners if b.company_id == "co1"] == ["c0", "c1", "c2"]


def test_one_failing_cluster_does_not_block_the_others():
    class FlakyLLM(FakeLLMClient):
        def generate_json(self, prompt, schema, *, tier, trace_id):
            if "Курс юаня" in prompt:
                raise TimeoutError("router timeout")
            return super().generate_json(prompt, schema, tier=tier, trace_id=trace_id)

    news = [make_news("n1", "c1", rerank_score=0.9),
            make_news("n2", "c2", title="Курс юаня вырос до 12,5 рубля", text="Курс юаня вырос до 12,5 рубля.",
                      rerank_score=0.8)]
    rt = build_branch(FlakyLLM(), StaticDirectory(COMPANIES))
    assert {b.key.cluster_id for b in rt.branch.handle_banner(make_req(news=news))} == {"c1"}


# --------------------------------------------------------------------------- вход оркестратора и проверка
def fabricate_first(times: int):
    """Первые `times` генераций подставляют выдуманное число, дальше ответ честный."""
    state = {"left": times}

    def tamper(draft):
        if state["left"] > 0:
            state["left"] -= 1
            draft["claims"][0]["text"] = "Банк России повысил ставку до 16%"
        return draft
    return tamper


def test_handle_returns_dtos_for_requesting_company_only():
    _, rt = runtime()
    dtos = rt.branch.handle(make_req())
    assert [d.banner_id for d in dtos] == banner_ids(rt, "co1") and not dtos[0].raw_news
    assert banner_ids(rt, "co2")                    # остальным компаниям сегмента баннеры тоже предрассчитаны


def test_handle_serves_stored_banners_without_generation():
    llm, rt = runtime()
    first = rt.branch.handle(make_req())
    rt.cache.invalidate_cluster("c1")               # кэш сегмента не нужен: баннер уже лежит в хранилище
    second = rt.branch.handle(make_req())
    assert llm.call_count == 1 and first == second


def test_handle_generates_only_missing_clusters():
    llm, rt = runtime()
    rt.branch.handle(make_req(news=[make_news("n1", "c1")]))
    dtos = rt.branch.handle(make_req(news=[make_news("n1", "c1"), make_news("n2", "c2", rerank_score=0.8)]))
    assert llm.call_count == 2 and len(dtos) == 2


def test_handle_skips_banner_hidden_by_company():
    _, rt = runtime()
    banner_id = rt.branch.handle(make_req())[0].banner_id
    rt.store.suppress("co1", banner_id, "не про меня")
    assert rt.branch.handle(make_req()) == []


def test_handle_dispatches_chat_by_mode():
    _, rt = runtime()
    assert rt.branch.handle(chat_req()).claims


def test_invalid_summary_is_regenerated_and_valid_one_is_published():
    llm, rt = runtime(FakeLLMClient(tamper=fabricate_first(1)))
    dto = rt.branch.handle(make_req())[0]
    assert llm.call_count == 2 and not dto.raw_news and not dto.caveat


def test_generation_attempts_are_capped_and_best_effort_is_saved():
    llm, rt = runtime(FakeLLMClient(tamper=fabricate_first(9)))
    dto = rt.branch.handle(make_req())[0]
    assert llm.call_count == BranchConfig().max_generation_attempts == 2
    assert dto.raw_news                              # выдуманное число: только новость и ссылка


def test_lite_budget_does_not_retry():
    llm, rt = runtime(FakeLLMClient(tamper=fabricate_first(9)))
    rt.branch.handle(make_req(budget=BudgetTier.LITE))
    assert llm.call_count == 1


def test_chat_does_not_regenerate_by_default_to_keep_latency():
    llm, rt = runtime(FakeLLMClient(tamper=fabricate_first(1)))
    answer = rt.branch.handle(chat_req())
    assert llm.call_count == 1 and answer.raw_news      # выдуманное число: только новость, без второго вызова


def test_chat_regenerates_invalid_answer_when_attempts_allowed():
    llm, rt = runtime(FakeLLMClient(tamper=fabricate_first(1)), config=BranchConfig(chat_generation_attempts=2))
    answer = rt.branch.handle(chat_req())
    assert llm.call_count == 2 and not answer.raw_news and answer.claims


def test_banners_persist_in_local_file_across_restart(tmp_path):
    config = BranchConfig(banner_store_path=str(tmp_path / "banners.db"))
    llm1, rt1 = runtime(config=config)
    first = rt1.branch.handle(make_req())
    rt1.store.close()                                # процесс «остановлен»

    llm2, rt2 = runtime(config=config)               # новый процесс: кэш сегментов пуст, баннеры в файле
    assert rt2.branch.handle(make_req()) == first
    assert llm1.call_count == 1 and llm2.call_count == 0
    rt2.store.close()
