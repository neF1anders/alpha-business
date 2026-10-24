"""Правила валидатора: любое число должно быть в источнике, у утверждения должны быть слова источника."""
from factories import make_company, make_draft, make_news, make_req, make_segment
from summary_branch import scoring
from summary_branch.confidence import DefaultConfidenceScorer
from summary_branch.config import BranchConfig
from summary_branch.generator import ValidatedGenerator
from summary_branch.models import ClaimType, Decision, DraftClaim


def decide(claim_text, news_text=None, **news_kw):
    news = make_news(**({"text": news_text} if news_text else {}), **news_kw)
    req = make_req(news=[news], company=make_company(tags=["rate", "credit"]))
    draft = make_draft([DraftClaim(type=ClaimType.FACT, text=claim_text, evidence_ids=[news.news_id])])
    seg = make_segment(req, draft)
    return seg, DefaultConfidenceScorer().score(seg, req.company)


def _validator():
    gen = ValidatedGenerator.__new__(ValidatedGenerator)
    gen._config = BranchConfig()
    return gen


def test_one_wrong_number_among_several_is_caught():
    seg, confidence = decide("Банк России снизил ставку с 15,0% до 14,0%",
                             news_text="Банк России снизил ставку с 16,0% до 14,0%.")
    assert seg.signals.numeric_miss and confidence.score <= 0.5
    assert confidence.decision != Decision.SHOW_SUMMARY and not _validator().is_valid(seg.signals)


def test_number_words_match_digits():
    seg, _ = decide("Маржа упала почти на 0,5%", news_text="Маржа сети за квартал упала почти на полпроцента.")
    assert not seg.signals.numeric_miss
    seg, _ = decide("Овердрафт подешевеет примерно на 1,5%", news_text="Ставка снизится примерно на полтора процента.")
    assert not seg.signals.numeric_miss


def test_claim_without_source_words_is_unsupported_even_without_numbers():
    seg, confidence = decide("Компания «Вектор-Альфа» объявила о слиянии с конкурентом",
                             news_text="Банк России сохранил ключевую ставку на уровне 14,0%.")
    assert seg.signals.unsupported_claim and not seg.signals.numeric_miss
    assert confidence.score <= 0.5 and "слабо подтверждено" in " ".join(confidence.reasons)
    assert not _validator().is_valid(seg.signals)


def test_faithful_paraphrase_passes_both_rules():
    seg, confidence = decide("Банк России сохранил ключевую ставку на уровне 14,0%",
                             news_text="Совет директоров Банка России сохранил ключевую ставку на уровне 14,0%.")
    assert not seg.signals.numeric_miss and not seg.signals.unsupported_claim
    assert _validator().is_valid(seg.signals)


def test_rules_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(scoring, "MIN_CLAIM_LEX", 0.0)
    monkeypatch.setattr(scoring, "STRICT_NUMERIC", False)
    seg, _ = decide("Компания «Вектор-Альфа» объявила о слиянии с конкурентом",
                    news_text="Банк России сохранил ключевую ставку на уровне 14,0%.")
    assert not seg.signals.unsupported_claim
