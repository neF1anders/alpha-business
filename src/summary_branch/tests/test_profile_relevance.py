"""Релевантность по профилю: тема, регион, косвенная связь, правила отношений, привязка «для вас» к клиентам."""
from datetime import timedelta

from factories import make_company, make_news, make_profile, make_req, make_segment
from summary_branch.models import Decision, PaymentProfile, Severity, utcnow
from summary_branch.personalization import enrich
from summary_branch.scoring import company_confidence, lead_from_news, render_for_you


def confidence(profile, topic="fx", tags=("fx",), severity=None, requested=False, **news_kw):
    req = make_req()
    seg = make_segment(req)
    lead = lead_from_news(make_news(tags=list(tags), **news_kw))
    company = enrich(make_company(tags=[], features=[], profile=profile))
    return company_confidence(seg.signals, lead, topic, company, now=utcnow(), severity=severity,
                              requested_by_user=requested)


def test_sensitive_company_scores_higher_than_insensitive_on_same_news():
    importer = confidence(make_profile(foreign_trade=True))
    domestic = confidence(make_profile())
    assert importer.score > domestic.score
    assert importer.components.personal_fit == 1.0 and domestic.components.personal_fit < 0.25


def test_insensitive_company_is_capped_and_explained_but_chat_user_is_not_penalised():
    domestic = confidence(make_profile())
    assert domestic.decision in (Decision.SHOW_RAW_NEWS, Decision.DROP)
    assert any("не чувствительна" in r for r in domestic.reasons)
    asked = confidence(make_profile(), requested=True)
    assert asked.score > domestic.score and not any("не чувствительна" in r for r in asked.reasons)


def test_counterparty_sector_gives_indirect_relevance():
    payments = PaymentProfile(counterparty_sectors={"46": 0.8})
    plain = confidence(make_profile(), tags=("fx", "okved:46"))
    linked = confidence(make_profile(payments=payments), tags=("fx", "okved:46"))
    assert linked.score > plain.score and not any("не чувствительна" in r for r in linked.reasons)


def test_news_about_other_region_lowers_relevance():
    local = confidence(make_profile(foreign_trade=True, region="Омская область"), tags=("fx", "region:омская область"))
    other = confidence(make_profile(foreign_trade=True, region="Омская область"), tags=("fx", "region:пермский край"))
    assert local.components.relevance > other.components.relevance


def test_negative_flag_caps_opportunity_to_caveat():
    clean = confidence(make_profile(foreign_trade=True), severity=Severity.OPPORTUNITY)
    flagged = confidence(make_profile(foreign_trade=True, negative_flag=True), severity=Severity.OPPORTUNITY)
    assert flagged.score < 0.75 and flagged.score <= clean.score
    assert confidence(make_profile(foreign_trade=True, negative_flag=True), severity=Severity.ATTENTION).score == clean.score


def test_stale_news_still_loses_to_fresh_for_same_company():
    profile = make_profile(foreign_trade=True)
    fresh = confidence(profile, published_at=utcnow() - timedelta(hours=2))
    stale = confidence(profile, published_at=utcnow() - timedelta(hours=200))
    assert fresh.components.freshness > stale.components.freshness and fresh.score > stale.score


def test_company_without_profile_keeps_tag_based_relevance():
    result = confidence(None, tags=("fx",))
    assert result.components.personal_fit is None


def test_for_you_never_speaks_about_bank_products_to_non_clients():
    template = "У вас {feature}: платёж по кредиту не снизится"
    fallback = "Ваш кредит подорожает"
    prospect = make_company(profile=make_profile(relationship="prospect"), features=["штат 16-100 человек"])
    assert render_for_you(template, "Нагрузка вырастет", prospect, "кредит ставка") == "Нагрузка вырастет"
    assert render_for_you(template, fallback, prospect, "кредит") == ""
    client = make_company(profile=make_profile(relationship="client"), features=["действующий кредит"])
    assert "действующий кредит" in render_for_you(template, fallback, client, "кредит ставка")


def test_prompt_with_profile_has_derived_facts_but_no_ids_groups_or_counterparties():
    from summary_branch.config import BranchConfig
    from summary_branch.summarizer import build_prompt
    profile = make_profile(foreign_trade=True, group_id="group_42_sb", revenue=123456789.0,
                           payments=PaymentProfile(counterparty_sectors={"46": 0.6}))
    company = make_company("secret-company-42", segment_id="secret-segment", tags=[], features=[], profile=profile)
    req = make_req(company=enrich(company))
    assert "secret-segment" in req.company.segment_id
    prompt = build_prompt(req, BranchConfig())
    for secret in ("secret-company-42", "secret-segment", "group_42", "123456789"):
        assert secret not in prompt
