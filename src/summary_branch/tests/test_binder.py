from factories import make_bank_material, make_news, make_req
from summary_branch.binder import DefaultEvidenceBinder
from summary_branch.config import BranchConfig
from summary_branch.models import ClaimType, DraftClaim, SourceKind, SummaryDraft


def make_draft(*claims: DraftClaim) -> SummaryDraft:
    return SummaryDraft(headline="Ставка 14%", for_you_template="У вас {feature}", for_you_fallback="Общий текст",
                        topic="rate", severity="attention", claims=list(claims))


def fact(text, *ids, type=ClaimType.FACT):
    return DraftClaim(type=type, text=text, evidence_ids=list(ids))


def test_fact_without_valid_reference_is_dropped_but_soft_claims_stay():
    draft = make_draft(
        fact("Ставка 14%"),                                   # нет ссылок
        fact("Ставка 14%", "ghost"),                          # ссылка на неизвестный id
        fact("Ставка 14% годовых", "n1"),
        fact("Нагрузка не снизится", type=ClaimType.MODEL),   # без ссылок остаётся
        fact("Возможна пауза до весны", type=ClaimType.HYPOTHESIS),
    )
    claims, _ = DefaultEvidenceBinder().bind(draft, make_req())
    assert [c.text for c in claims] == ["Ставка 14% годовых", "Нагрузка не снизится", "Возможна пауза до весны"]


def test_unknown_ids_are_filtered_from_soft_claim_evidence():
    claims, _ = DefaultEvidenceBinder().bind(make_draft(fact("Вывод", "ghost", "n1", type=ClaimType.MODEL)), make_req())
    assert [e.ref_id for e in claims[0].evidence] == ["n1"]


def test_duplicates_collapse_and_merge_evidence():
    req = make_req(news=[make_news("n1"), make_news("n2", url="https://m.ru/y", source_kind=SourceKind.MEDIA)])
    draft = make_draft(fact("Ставка сохранена на уровне 14%", "n1"), fact("  ставка СОХРАНЕНА на уровне 14%!", "n2"))
    claims, _ = DefaultEvidenceBinder().bind(draft, req)
    assert len(claims) == 1
    assert [e.ref_id for e in claims[0].evidence] == ["n1", "n2"]


def test_sources_order_primary_bank_media_and_limit_three():
    media = make_news("n2", url="https://media.ru/a", source_kind=SourceKind.MEDIA, primary_url="https://cbr.ru/p")
    req = make_req(news=[media, make_news("n3", url="https://media.ru/b", source_kind=SourceKind.MEDIA)],
                   bank_materials=[make_bank_material()])
    draft = make_draft(fact("Ставка 14%", "n2"), fact("Ставка 14%, версия 2", "n3"), fact("Банк ждёт ставку 14%", "d1"))
    _, sources = DefaultEvidenceBinder().bind(draft, req)
    assert [s.kind for s in sources] == [SourceKind.PRIMARY, SourceKind.BANK, SourceKind.MEDIA]
    assert sources[0].url == "https://cbr.ru/p"
    assert sources[1].title == "Макрообзор, стр. 4"


def test_bank_material_without_url_is_rejected_by_contract():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        make_bank_material(url=None)


def test_quote_is_within_limit_and_keeps_claim_number():
    long_text = "Вводное предложение без цифр. " + "Слово " * 60 + "ставка составила 14,0% годовых и осталась прежней."
    req = make_req(news=[make_news(title="Решение регулятора", text=long_text)])
    config = BranchConfig(quote_max_chars=80)
    claims, _ = DefaultEvidenceBinder(config).bind(make_draft(fact("Ставка 14%", "n1")), req)
    quote = claims[0].evidence[0].quote
    assert 0 < len(quote) <= 80
    assert "14,0" in quote


def test_quote_picks_sentence_with_matching_number():
    req = make_req(news=[make_news(title="Решение регулятора")])
    claims, _ = DefaultEvidenceBinder().bind(make_draft(fact("Ставка 14%", "n1")), req)
    assert "14,0%" in claims[0].evidence[0].quote
