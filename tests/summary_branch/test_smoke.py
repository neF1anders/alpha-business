from datetime import timedelta

import pytest

from summary_branch.domain.models import *
from summary_branch.domain.scoring import (
    company_confidence, content_signals, lead_from_news, pick_top_k, render_for_you,
)


def make_req(**kw):
    now = utcnow()
    news = RankedNewsItem(
        news_id="n1", cluster_id="c1", title="Банк России сохранил ключевую ставку 14%",
        text="Совет директоров Банка России 11 сентября сохранил ключевую ставку на уровне 14,0%. Это первая пауза.",
        source_kind=SourceKind.PRIMARY, url="https://cbr.ru/x",
        published_at=now - timedelta(hours=3), tags=["rate", "credit"], rerank_score=0.9)
    company = CompanyContext(company_id="co1", segment_id="metal-large-north", tags=["rate", "credit", "metal"],
                             features=["3 кредитных продукта"])
    base = dict(trace_id="t1", mode=Mode.BANNER, company=company, news=[news])
    base.update(kw)
    return SummaryRequest(**base)


def good_draft():
    return SummaryDraft(headline="Ставка остаётся 14%", for_you_template="У вас {feature}: нагрузка не снизится",
                        for_you_fallback="Нагрузка по кредитам не снизится", topic="rate", severity=Severity.ATTENTION,
                        claims=[])


def good_claims():
    return [
        Claim(type=ClaimType.FACT, text="Банк России сохранил ключевую ставку 14%",
              evidence=[EvidenceRef(ref_id="n1", quote="сохранил ключевую ставку на уровне 14,0%")]),
        Claim(type=ClaimType.MODEL, text="Нагрузка по кредитам не снизится", evidence=[EvidenceRef(ref_id="n1")]),
    ]


SRC = [SourceRef(kind=SourceKind.PRIMARY, title="ЦБ", url="https://cbr.ru/x")]


def test_request_contract():
    with pytest.raises(ValueError):
        make_req(question="лишний вопрос")
    with pytest.raises(ValueError):
        make_req(mode=Mode.CHAT)
    assert make_req(mode=Mode.CHAT, question="что со ставкой?").question


def test_confidence_levels_and_guards():
    req = make_req()
    sig = content_signals(req, good_draft(), good_claims(), SRC)
    assert sig.structure_ok and sig.sqs > 0.6
    lead = lead_from_news(req.news[0])
    c = company_confidence(sig, lead, "rate", req.company)
    assert c.decision == Decision.SHOW_SUMMARY

    bad = good_claims()
    bad[0].text = "Банк России повысил ключевую ставку до 16%"          # выдуманное число
    sig2 = content_signals(req, good_draft(), bad, SRC)
    assert sig2.numeric_miss
    c2 = company_confidence(sig2, lead, "rate", req.company)
    assert c2.score <= 0.5 and c2.decision != Decision.SHOW_SUMMARY

    assert company_confidence(sig, lead, "rate", req.company, has_sources=False).decision == Decision.DROP

    other = CompanyContext(company_id="co2", segment_id="s", tags=["agro"])       # не про эту компанию
    low = company_confidence(sig, lead_from_news(req.news[0]).model_copy(update={"rerank_score": 0.1}), "rate", other)
    assert low.decision in (Decision.SHOW_RAW_NEWS, Decision.DROP)


def test_for_you_render():
    req = make_req()
    d = good_draft()
    text = " ".join(c.text for c in good_claims())
    assert "3 кредитных продукта" in render_for_you(d.for_you_template, d.for_you_fallback, req.company, text)
    nobody = CompanyContext(company_id="x", segment_id="s", features=["экспорт в ЕАЭС"])
    assert render_for_you(d.for_you_template, d.for_you_fallback, nobody, text) == d.for_you_fallback


def test_selection_diversity():
    key = ContentKey(cluster_id="c", segment_id="s", prompt_version="v0")

    def mk(i, topic, sev):
        return Banner(banner_id=str(i), company_id="co1", key=key, topic=topic, severity=sev,
                      for_you="f", confidence=0.9, decision=Decision.SHOW_SUMMARY)
    bs = [mk(1, "rate", Severity.ATTENTION), mk(2, "rate", Severity.ATTENTION), mk(3, "labor", Severity.WATCH)]
    assert {b.topic for b in pick_top_k(bs, 2)} == {"rate", "labor"}


def test_for_you_empty_template_falls_back():
    req = make_req()
    assert render_for_you("", "Общий текст", req.company, "кредиты") == "Общий текст"
    assert render_for_you("Без слота", "Общий текст", req.company, "кредиты") == "Без слота"
