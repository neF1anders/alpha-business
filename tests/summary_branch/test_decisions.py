"""Критерии 5 и 6: числа, уровни уверенности и вид баннера."""
import pytest
from factories import make_company, make_draft, make_news, make_req, make_segment
from summary_branch.application.composer import DefaultBannerComposer, make_banner_id
from summary_branch.application.confidence import DefaultConfidenceScorer
from summary_branch.domain.models import ClaimType, Decision, DraftClaim
from summary_branch.application.personal import fallback_text
from summary_branch.application.publisher import DefaultPublisher

scorer = DefaultConfidenceScorer()
composer = DefaultBannerComposer()
publisher = DefaultPublisher()


def decide(rerank_score=0.9, tags=("rate", "credit"), draft=None):
    req = make_req(news=[make_news(rerank_score=rerank_score)], company=make_company(tags=list(tags)))
    seg = make_segment(req, draft)
    confidence = scorer.score(seg, req.company)
    return seg, req.company, confidence


def build(seg, company, confidence):
    banner = composer.compose(seg, company, confidence, fallback_text(seg, company, []))
    return banner, publisher.to_dto(banner, seg)


def test_invented_number_caps_score_and_blocks_show_summary():
    fabricated = make_draft([DraftClaim(type=ClaimType.FACT, text="Банк России повысил ставку до 16%",
                                        evidence_ids=["n1"])])
    seg, company, confidence = decide(draft=fabricated)
    assert seg.signals.numeric_miss
    assert confidence.score <= 0.5
    assert confidence.decision != Decision.SHOW_SUMMARY


def test_high_confidence_gives_plain_banner():
    banner, dto = build(*decide())
    assert banner.decision == Decision.SHOW_SUMMARY
    assert not dto.caveat and not dto.raw_news
    assert "3 кредитных продукта" in dto.for_you
    assert dto.label == "Внимание · ставка"
    assert dto.sources


def test_medium_confidence_gives_caveat():
    banner, dto = build(*decide(rerank_score=0.5, tags=("agro",)))
    assert banner.decision == Decision.SHOW_WITH_CAVEAT
    assert dto.caveat and not dto.raw_news and dto.for_you


def test_low_confidence_gives_raw_news_with_empty_for_you():
    seg, company, confidence = decide(rerank_score=0.2, tags=("agro",))
    banner, dto = build(seg, company, confidence)
    assert banner.decision == Decision.SHOW_RAW_NEWS
    assert dto.raw_news and dto.for_you == "" and banner.for_you == ""
    assert dto.headline == seg.sources[0].title
    assert len(dto.sources) == 1


def test_no_sources_means_drop_and_no_banner():
    seg, company, _ = decide()
    sourceless = seg.model_copy(update={"sources": []})
    confidence = scorer.score(sourceless, company)
    assert confidence.decision == Decision.DROP
    with pytest.raises(ValueError):
        composer.compose(sourceless, company, confidence, fallback_text(sourceless, company, []))


def test_banner_id_is_deterministic_and_company_specific():
    seg, company, _ = decide()
    assert make_banner_id("co1", seg.key) == make_banner_id("co1", seg.key)
    assert make_banner_id("co1", seg.key) != make_banner_id("co2", seg.key)


def test_expiry_follows_topic():
    seg, company, confidence = decide()
    text = fallback_text(seg, company, [])
    banner = composer.compose(seg, company, confidence, text)
    assert round((banner.expires_at - banner.created_at).total_seconds() / 3600) == 168   # rate
    fx = composer.compose(seg.model_copy(update={"topic": "fx"}), company, confidence, text)
    assert round((fx.expires_at - fx.created_at).total_seconds() / 3600) == 36
    labor = composer.compose(seg.model_copy(update={"topic": "labor"}), company, confidence, text)
    assert round((labor.expires_at - labor.created_at).total_seconds() / 3600) == 120
    other = composer.compose(seg.model_copy(update={"topic": "other"}), company, confidence, text)
    assert round((other.expires_at - other.created_at).total_seconds() / 3600) == 36


def test_publisher_push_calls_handler():
    received = []
    pub = DefaultPublisher(push_handler=lambda cid, dtos: received.append((cid, dtos)))
    _, dto = build(*decide())
    pub.push("co1", [dto])
    assert received == [("co1", [dto])]
    DefaultPublisher().push("co1", [dto])  # без обработчика не падает
