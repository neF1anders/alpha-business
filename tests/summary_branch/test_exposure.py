"""Пути влияния новости на компанию и проверка персонального текста."""
from factories import make_company, make_news, make_profile, make_req, make_segment
from summary_branch.application.personal import fallback_text, validation_problem
from summary_branch.domain.exposure import exposure_paths, news_exposure_paths, personal_exposure
from summary_branch.domain.models import (
    CounterpartySector, ExposureKind, ImpactDirection, PaymentProfile, PersonalDraft, Product, SectorImpact,
)


def summary(impacts=(), topic="other", countries=(), regions=(), tags=()):
    req = make_req(news=[make_news(tags=list(tags))])
    seg = make_segment(req)
    return seg.model_copy(update={
        "topic": topic, "countries": list(countries), "regions": list(regions),
        "impacts": [SectorImpact(division=d, direction=ImpactDirection.NEGATIVE, mechanism=f"механизм {d}",
                                 evidence_ids=["n1"]) for d in impacts]})


def company(**profile):
    return make_company(profile=make_profile(**profile))


def kinds(paths):
    return [p.kind for p in paths]


def test_no_profile_means_no_paths():
    assert exposure_paths(summary(impacts=["47"]), make_company()) == []


def test_own_sector_is_strongest_path_and_carries_mechanism():
    paths = exposure_paths(summary(impacts=["47"]), company())
    assert kinds(paths) == [ExposureKind.OWN_SECTOR]
    assert paths[0].weight == 1.0 and paths[0].mechanism == "механизм 47" and not paths[0].hypothesis
    assert paths[0].path_id == "p1"


def test_rate_news_hits_client_with_loan_through_product():
    paths = exposure_paths(summary(topic="rate"), company(products={Product.LOAN}))
    assert kinds(paths) == [ExposureKind.PRODUCT] and "ключевая ставка" in paths[0].explanation


def test_prospect_products_are_not_used():
    paths = exposure_paths(summary(topic="rate"), company(relationship="prospect", products={Product.LOAN}))
    assert ExposureKind.PRODUCT not in kinds(paths)


def test_foreign_trade_country_and_region_paths():
    paths = exposure_paths(summary(countries=["КИТАЙ"], regions=["Тверская область"]),
                           company(trade_countries=["китай", "турция"]))
    assert ExposureKind.FOREIGN_TRADE in kinds(paths) and ExposureKind.REGION in kinds(paths)
    assert "Китай" in paths[0].explanation


def test_counterparty_paths_weight_by_flow_share_and_are_hypotheses():
    payments = PaymentProfile(
        suppliers=[CounterpartySector(division="24", name="металлургия", share=0.6),
                   CounterpartySector(division="10", share=0.02)],
        buyers=[CounterpartySector(division="41", share=0.1)])
    paths = exposure_paths(summary(impacts=["24", "10", "41"]), company(payments=payments))
    assert kinds(paths) == [ExposureKind.SUPPLIERS, ExposureKind.BUYERS]       # 2% потока — шум
    assert paths[0].weight == 1.0 and "основная часть потока" in paths[0].explanation
    assert "строительство" in paths[1].explanation and "10% входящих" in paths[1].explanation
    assert all(p.hypothesis for p in paths)


def test_affiliates_path_skips_own_sector():
    paths = exposure_paths(summary(impacts=["47", "68"]), company(affiliate_sectors=["47", "68"]))
    assert kinds(paths) == [ExposureKind.OWN_SECTOR, ExposureKind.AFFILIATES]


def test_paths_fall_back_to_news_tags_without_impact_map():
    assert kinds(exposure_paths(summary(tags=["okved:47"]), company())) == [ExposureKind.OWN_SECTOR]
    assert kinds(news_exposure_paths(company(), ["retail"])) == [ExposureKind.OWN_SECTOR]


def test_several_weak_paths_add_up_but_stay_below_one():
    payments = PaymentProfile(suppliers=[CounterpartySector(division="24", share=0.1)],
                              buyers=[CounterpartySector(division="41", share=0.1)])
    paths = exposure_paths(summary(impacts=["24", "41"]), company(payments=payments))
    single = paths[0].weight
    assert single < personal_exposure(paths) < 1.0


# --------------------------------------------------------------------------- персональный текст
def _paths():
    payments = PaymentProfile(suppliers=[CounterpartySector(division="24", name="металлургия", share=0.4)])
    co = company(payments=payments)
    seg = summary(impacts=["24"])
    return seg, co, exposure_paths(seg, co)


def draft(**kw):
    base = dict(headline="Металл дорожает", for_you="Поставщики из металлургии — 40% платежей: закупки могут подорожать",
                path_ids=["p1"])
    base.update(kw)
    return PersonalDraft(**base)


def test_valid_personal_text_passes():
    seg, co, paths = _paths()
    assert validation_problem(draft(), seg, co, paths) == ""


def test_personal_text_rejects_new_numbers_unknown_paths_advice_and_off_path_text():
    seg, co, paths = _paths()
    assert validation_problem(draft(for_you="Поставщики из металлургии поднимут цены на 35%"), seg, co, paths)
    assert validation_problem(draft(path_ids=["p9"]), seg, co, paths)
    assert validation_problem(draft(for_you="Поставщики из металлургии: рекомендуем купить прокат"), seg, co, paths)
    assert validation_problem(draft(for_you="Учтите это при планировании бюджета"), seg, co, paths)


def test_personal_text_rejects_bank_product_claim_for_prospect():
    seg, _, _ = _paths()
    prospect = company(relationship="prospect", payments=PaymentProfile(
        suppliers=[CounterpartySector(division="24", name="металлургия", share=0.4)]))
    paths = exposure_paths(seg, prospect)
    assert validation_problem(draft(for_you="Поставщики из металлургии — 40%: ваш кредит подорожает"),
                              seg, prospect, paths)


def test_fallback_text_names_best_path_and_hedges_hypothesis():
    seg, co, paths = _paths()
    text = fallback_text(seg, co, paths)
    assert text.for_you.startswith("Может касаться вас. Поставщики из отрасли «металлургия» — 40%")
    assert "Механизм 24" in text.for_you and text.path_ids == ("p1",) and not text.from_model


def test_impact_map_keeps_only_grounded_okved_divisions():
    from factories import make_draft
    from summary_branch.application.impact import bind_impacts
    from summary_branch.domain.models import SectorImpactDraft
    req = make_req()
    impacts = [SectorImpactDraft(division="41", direction="negative", mechanism="Ставка 14% дорожит ипотеку",
                                 evidence_ids=["n1"]),
               SectorImpactDraft(division="4", direction="mixed", mechanism="Спрос на кредиты", evidence_ids=["n1"]),
               SectorImpactDraft(division="строй", direction="negative", mechanism="Спрос", evidence_ids=["n1"]),
               SectorImpactDraft(division="68", direction="negative", mechanism="Цены упадут на 20%",
                                 evidence_ids=["n1"]),
               SectorImpactDraft(division="47", direction="negative", mechanism="Спрос", evidence_ids=["ghost"])]
    bound = bind_impacts(make_draft(impacts=impacts), req)
    assert [i.division for i in bound] == ["41", "04"]
