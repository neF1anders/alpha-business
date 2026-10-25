"""Сквозные тесты ветки: кластер разбирается один раз, баннер персонален для каждой компании."""
import logging
import threading

import pytest
from factories import make_company, make_news, make_profile, make_req
from summary_branch.bootstrap import build_branch
from summary_branch.config import BranchConfig
from summary_branch.domain import ports as ifc
from summary_branch.domain.models import BudgetTier, CounterpartySector, Mode, PaymentProfile, Product
from summary_branch.infrastructure.fakes import FakeLLMClient

STEEL_NEWS = dict(news_id="s1", cluster_id="steel", title="Металлурги подняли цены на прокат на 12%",
                  text="Крупнейшие металлурги подняли цены на прокат на 12%. Причина — рост издержек на сырьё.",
                  tags=["okved:24"])


def wholesale_with_metal_suppliers(company_id="buyer", share=0.4):
    profile = make_profile(okved_code="46.72", okved_name="Торговля оптовая металлами", region="Москва",
                           products={Product.LOAN},
                           payments=PaymentProfile(suppliers=[CounterpartySector(division="24", name="металлургия",
                                                                                 share=share, counterparties=3)]))
    return make_company(company_id, tags=[], features=[], profile=profile)


def unrelated_company(company_id="cafe"):
    profile = make_profile(okved_code="56.10", okved_name="Деятельность ресторанов", relationship="prospect")
    return make_company(company_id, tags=[], features=[], profile=profile)


def runtime(llm=None, **kw):
    llm = llm or FakeLLMClient()
    return llm, build_branch(llm, **kw)


def steel_req(company, **kw):
    return make_req(company=company, news=[make_news(**STEEL_NEWS)], **kw)


def test_components_implement_protocols():
    llm, rt = runtime()
    assert isinstance(rt.branch, ifc.SummaryBranch)
    assert isinstance(rt.store, ifc.BannerStore) and isinstance(rt.cache, ifc.SummaryCache)
    assert isinstance(llm, ifc.LLMClient)


def test_counterparty_path_personalizes_banner_through_suppliers():
    llm, rt = runtime()
    dto = rt.branch.banner(steel_req(wholesale_with_metal_suppliers()))
    assert dto is not None and not dto.raw_news
    assert "поставщики из отрасли «металлургия» — 40% исходящих платежей" in dto.for_you.lower()
    assert dto.cluster_id == "steel" and dto.sources[0].ref_id == "s1"
    assert len(llm.calls_of("summary")) == 1 and len(llm.calls_of("personal")) == 1


def test_cluster_is_summarized_once_for_all_companies_and_store_serves_repeat():
    llm, rt = runtime()
    first = rt.branch.banner(steel_req(wholesale_with_metal_suppliers("a")))
    rt.branch.banner(steel_req(wholesale_with_metal_suppliers("b", share=0.2)))
    assert len(llm.calls_of("summary")) == 1                   # разбор кластера общий
    assert len(llm.calls_of("personal")) == 2                  # текст у каждой компании свой
    assert rt.branch.banner(steel_req(wholesale_with_metal_suppliers("a"))) == first
    assert llm.call_count == 3                                 # повтор сессии целиком из хранилища


def test_company_without_exposure_gets_news_only_and_no_personal_call():
    llm, rt = runtime()
    dto = rt.branch.banner(steel_req(unrelated_company()))
    assert dto is not None and dto.raw_news and dto.for_you == ""
    assert llm.calls_of("personal") == []


def test_company_without_profile_falls_back_to_tags_and_template():
    llm, rt = runtime()
    dto = rt.branch.banner(make_req())
    assert dto is not None and "3 кредитных продукта" in dto.for_you
    assert llm.calls_of("personal") == []


def test_invalid_personal_text_is_replaced_by_path_based_text():
    def fabricate(draft):
        draft["for_you"] = "Ваши закупки подорожают на 35%"
        return draft
    _, rt = runtime(FakeLLMClient(personal_tamper=fabricate))
    dto = rt.branch.banner(steel_req(wholesale_with_metal_suppliers()))
    assert "35%" not in dto.for_you
    assert dto.for_you.startswith("Может касаться вас. Поставщики из отрасли «металлургия» — 40%")


def test_personal_model_failure_does_not_lose_banner():
    class PersonalDown(FakeLLMClient):
        def generate_json(self, prompt, schema, *, tier, trace_id):
            if "<<<PATHS>>>" in prompt:
                raise TimeoutError("router timeout")
            return super().generate_json(prompt, schema, tier=tier, trace_id=trace_id)
    _, rt = runtime(PersonalDown())
    dto = rt.branch.banner(steel_req(wholesale_with_metal_suppliers()))
    assert dto is not None and "металлургия" in dto.for_you


def test_parallel_sessions_of_one_cluster_wait_for_single_generation():
    started = threading.Event()

    class SlowLLM(FakeLLMClient):
        def generate_json(self, prompt, schema, *, tier, trace_id):
            started.set()
            threading.Event().wait(0.2)
            return super().generate_json(prompt, schema, tier=tier, trace_id=trace_id)

    llm, rt = runtime(SlowLLM())
    threads = [threading.Thread(target=rt.branch.banner, args=(steel_req(wholesale_with_metal_suppliers(f"c{i}")),))
               for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(llm.calls_of("summary")) == 1


def test_cached_only_without_cache_hit_makes_no_call_and_does_not_raise():
    llm, rt = runtime()
    assert rt.branch.banner(make_req(budget=BudgetTier.CACHED_ONLY)) is None
    assert llm.call_count == 0


def test_broken_json_retry_then_skip_without_exception(caplog):
    llm, rt = runtime(FakeLLMClient(broken_responses=1))
    assert rt.branch.banner(make_req())                          # один повтор спасает
    assert llm.call_count == 2

    llm2, rt2 = runtime(FakeLLMClient(broken_responses=9))
    with caplog.at_level(logging.WARNING):
        assert rt2.branch.banner(make_req()) is None
    assert llm2.call_count == 2
    assert "кластер пропущен" in caplog.text and "t1" in caplog.text


def test_fabricated_number_never_gets_plain_summary():
    def fabricate(draft):
        draft["claims"][0]["text"] = "Банк России повысил ставку до 16%"
        return draft
    _, rt = runtime(FakeLLMClient(tamper=fabricate))
    dto = rt.branch.banner(make_req())
    assert dto is not None and dto.raw_news


def test_cluster_without_supported_facts_creates_no_banner():
    def strip_evidence(draft):
        for claim in draft["claims"]:
            claim["evidence_ids"] = ["ghost"]
        return draft
    _, rt = runtime(FakeLLMClient(tamper=strip_evidence))
    assert rt.branch.banner(make_req()) is None


def test_new_banner_is_pushed_once():
    pushed = []
    _, rt = runtime(push_handler=lambda cid, dtos: pushed.append((cid, [d.banner_id for d in dtos])))
    dto = rt.branch.banner(make_req())
    assert pushed == [("co1", [dto.banner_id])]
    rt.branch.banner(make_req())
    assert len(pushed) == 1


def test_banner_hidden_by_company_is_not_regenerated():
    llm, rt = runtime()
    dto = rt.branch.banner(make_req())
    rt.store.hide("co1", dto.banner_id)
    rt.cache.invalidate_cluster("c1")
    assert rt.branch.banner(make_req()) is None
    assert llm.call_count == 1


def test_runtime_invalidate_cluster_forces_resummarization_and_new_push():
    pushed = []
    llm, rt = runtime(push_handler=lambda cid, dtos: pushed.append(cid))
    rt.branch.banner(make_req())
    assert rt.invalidate_cluster("c1", "новое событие") == 1
    rt.branch.banner(make_req())
    assert llm.call_count == 2 and len(pushed) == 2


def test_serve_banners_keeps_cluster_order_and_isolates_failures():
    class FlakyLLM(FakeLLMClient):
        def generate_json(self, prompt, schema, *, tier, trace_id):
            if "Курс юаня" in prompt:
                raise TimeoutError("router timeout")
            return super().generate_json(prompt, schema, tier=tier, trace_id=trace_id)

    news = [make_news("n1", "c1", rerank_score=0.9),
            make_news("n2", "c2", title="Курс юаня вырос до 12,5 рубля", text="Курс юаня вырос до 12,5 рубля.",
                      rerank_score=0.8),
            make_news("n3", "c3", rerank_score=0.7)]
    _, rt = runtime(FlakyLLM())
    assert [d.cluster_id for d in rt.branch.serve_banners(make_req(news=news))] == ["c1", "c3"]


def test_only_top_clusters_are_served():
    news = [make_news(f"n{i}", f"c{i}", rerank_score=0.5 + i / 20) for i in range(5)]
    llm, rt = runtime()
    dtos = rt.branch.serve_banners(make_req(news=news))
    assert llm.call_count == BranchConfig().max_clusters_per_request == 3
    assert [d.cluster_id for d in dtos] == ["c4", "c3", "c2"]


def test_banners_persist_in_local_file_across_restart(tmp_path):
    config = BranchConfig(banner_store_path=str(tmp_path / "banners.db"))
    llm1, rt1 = runtime(config=config)
    first = rt1.branch.banner(make_req())
    rt1.store.close()                                # процесс «остановлен»

    llm2, rt2 = runtime(config=config)               # новый процесс: кэш кластеров пуст, баннеры в файле
    assert rt2.branch.banner(make_req()) == first
    assert llm1.call_count == 1 and llm2.call_count == 0
    rt2.store.close()


# --------------------------------------------------------------------------- чат
def chat_req(**kw):
    return make_req(mode=Mode.CHAT, question="что со ставкой?", **kw)


def fabricate_first(times: int):
    """Первые `times` генераций подставляют выдуманное число, дальше ответ честный."""
    state = {"left": times}

    def tamper(draft):
        if state["left"] > 0:
            state["left"] -= 1
            draft["claims"][0]["text"] = "Банк России повысил ставку до 16%"
        return draft
    return tamper


def test_chat_answer_is_not_written_to_banner_store():
    _, rt = runtime()
    answer = rt.branch.handle_chat(chat_req())
    assert not answer.raw_news and answer.claims and answer.sources and answer.follow_ups
    assert rt.store.top_k("co1", 10) == []


def test_chat_prompt_carries_exposure_paths_of_company():
    llm, rt = runtime()
    rt.branch.banner(steel_req(wholesale_with_metal_suppliers()))       # карта влияния кластера уже в кэше
    rt.branch.handle_chat(make_req(mode=Mode.CHAT, question="Что с ценами на металл?",
                                   company=wholesale_with_metal_suppliers(), news=[make_news(**STEEL_NEWS)]))
    assert "поставщики из отрасли «металлургия» — 40% исходящих платежей" in llm.calls[-1].prompt


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


def test_chat_does_not_regenerate_by_default_to_keep_latency():
    llm, rt = runtime(FakeLLMClient(tamper=fabricate_first(1)))
    answer = rt.branch.handle(chat_req())
    assert llm.call_count == 1 and answer.raw_news


def test_chat_regenerates_invalid_answer_when_attempts_allowed():
    llm, rt = runtime(FakeLLMClient(tamper=fabricate_first(1)), config=BranchConfig(chat_generation_attempts=2))
    answer = rt.branch.handle(chat_req())
    assert llm.call_count == 2 and not answer.raw_news and answer.claims


def test_invalid_cluster_summary_is_regenerated_and_valid_one_is_published():
    llm, rt = runtime(FakeLLMClient(tamper=fabricate_first(1)))
    dto = rt.branch.handle(make_req())[0]
    assert llm.call_count == 2 and not dto.raw_news and not dto.caveat


def test_lite_budget_does_not_retry():
    llm, rt = runtime(FakeLLMClient(tamper=fabricate_first(9)))
    rt.branch.banner(make_req(budget=BudgetTier.LITE))
    assert llm.call_count == 1
