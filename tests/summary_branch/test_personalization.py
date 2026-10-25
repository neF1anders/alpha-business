"""Профиль компании: нормализация, чувствительность к темам, сегмент, признаки «для вас»."""
from factories import make_company, make_profile
from summary_branch.models import PaymentProfile
from summary_branch.personalization import (
    derive_features, derive_tags, enrich, indirect_exposure, news_sectors, segment_signature, topic_exposure,
)


def value(profile, topic):
    return topic_exposure(profile)[topic].value


def test_okved_code_is_normalised_and_placeholder_revenue_dropped():
    profile = make_profile(okved_code="41.2", revenue=1000.0)
    assert profile.okved_code == "41.20" and profile.revenue is None
    assert make_profile(okved_code="68.20.2", revenue=5e6).okved_code == "68.20.2"


def test_trade_countries_imply_foreign_trade_flag():
    assert make_profile(trade_countries=["КИТАЙ"]).foreign_trade is True


def test_foreign_trade_makes_company_fx_sensitive_and_others_not():
    assert value(make_profile(foreign_trade=True), "fx") == 1.0
    assert value(make_profile(), "fx") < 0.5


def test_debt_product_drives_rate_only_for_clients():
    assert value(make_profile(products={"loan"}), "rate") == 1.0
    assert value(make_profile(products={"loan"}, relationship="prospect"), "rate") < 0.5


def test_large_staff_drives_labor():
    assert value(make_profile(workers_band="101-250"), "labor") == 1.0
    assert value(make_profile(workers_band="Менее 15"), "labor") < 0.5


def test_signature_depends_on_sensitivity_and_relationship_not_identity():
    a = make_profile(foreign_trade=True, region="Омская область")
    b = make_profile(foreign_trade=True, region="Пермский край", group_id="g1")
    assert segment_signature(a) == segment_signature(b)
    assert segment_signature(a) != segment_signature(make_profile())
    assert segment_signature(make_profile(relationship="prospect")).endswith("prospect")


def test_features_state_only_profile_facts_and_hide_products_of_prospects():
    client = make_profile(products={"leasing"}, foreign_trade=True, trade_countries=["КИТАЙ", "ТУРЦИЯ"])
    assert "действующий лизинг" in derive_features(client)
    assert "внешнеторговая деятельность: Китай, Турция" in derive_features(client)
    assert not any("лизинг" in f for f in derive_features(make_profile(products={"leasing"}, relationship="prospect")))


def test_features_do_not_expose_ids_or_amounts():
    profile = make_profile(revenue=123456789.0, group_id="group_42_sb",
                           payments=PaymentProfile(counterparty_sectors={"46": 0.6}))
    text = " ".join(derive_features(profile) + derive_tags(profile))
    assert "123456789" not in text and "group_42" not in text


def test_indirect_exposure_uses_counterparty_sector_share():
    profile = make_profile(payments=PaymentProfile(counterparty_sectors={"46": 0.6, "62": 0.2}))
    assert indirect_exposure(profile, news_sectors(["rate", "okved:46"])) == 0.3
    assert indirect_exposure(profile, set()) == 0.0
    assert indirect_exposure(make_profile(), {"46"}) == 0.0


def test_enrich_merges_without_duplicates_and_keeps_company_without_profile():
    company = make_company(tags=["rate"], features=["3 кредитных продукта"], profile=make_profile(products={"loan"}))
    enriched = enrich(company)
    assert enriched.tags.count("rate") == 1 and "okved:47" in enriched.tags
    assert enriched.features[0] == "3 кредитных продукта" and "действующий кредит" in enriched.features
    bare = make_company()
    assert enrich(bare) is bare
