"""Общие фабрики тестовых данных."""
from datetime import timedelta

from summary_branch.domain.models import (
    BankMaterialRef, CompanyContext, CompanyProfile, Mode, RankedNewsItem, SourceKind, SummaryRequest, utcnow,
)

RATE_TEXT = ("Совет директоров Банка России 11 сентября сохранил ключевую ставку на уровне 14,0%. "
             "Это первая пауза.")


def make_news(news_id="n1", cluster_id="c1", **kw) -> RankedNewsItem:
    base = dict(news_id=news_id, cluster_id=cluster_id, title="Банк России сохранил ключевую ставку 14%",
                text=RATE_TEXT, source_kind=SourceKind.PRIMARY, url="https://cbr.ru/x",
                published_at=utcnow() - timedelta(hours=3), tags=["rate", "credit"], rerank_score=0.9)
    base.update(kw)
    return RankedNewsItem(**base)


def make_company(company_id="co1", segment_id="metal-large-north", **kw) -> CompanyContext:
    base = dict(company_id=company_id, segment_id=segment_id, tags=["rate", "credit", "metal"],
                features=["3 кредитных продукта"])
    base.update(kw)
    return CompanyContext(**base)


def make_profile(**kw) -> CompanyProfile:
    """Вымышленный профиль: розничная компания, клиент банка без кредитов и ВЭД."""
    base = dict(okved_code="47.11", okved_name="Торговля розничная в неспециализированных магазинах",
                workers_band="Менее 15", region="Тверская область", relationship="client")
    base.update(kw)
    return CompanyProfile(**base)


def make_bank_material(doc_id="d1", **kw) -> BankMaterialRef:
    base = dict(doc_id=doc_id, title="Макрообзор", page=4,
                claim_text="Аналитики банка ожидают сохранения ставки 14% до конца года.",
                url="https://alfabank.ru/doc")
    base.update(kw)
    return BankMaterialRef(**base)


def make_req(**kw) -> SummaryRequest:
    base = dict(trace_id="t1", mode=Mode.BANNER, company=make_company(), news=[make_news()])
    base.update(kw)
    return SummaryRequest(**base)


def make_draft(claims=None, **kw):
    from summary_branch.domain.models import ClaimType, DraftClaim, SummaryDraft
    base = dict(headline="Ставка остаётся 14%", for_you_template="У вас {feature}: нагрузка не снизится",
                for_you_fallback="Нагрузка по кредитам не снизится", topic="rate", severity="attention",
                claims=claims if claims is not None else [
                    DraftClaim(type=ClaimType.FACT, text="Банк России сохранил ключевую ставку на уровне 14%",
                               evidence_ids=["n1"]),
                    DraftClaim(type=ClaimType.MODEL, text="Нагрузка по кредитам не снизится", evidence_ids=["n1"]),
                ])
    base.update(kw)
    return SummaryDraft(**base)


def make_segment(req, draft=None):
    """Саммари кластера как его собрал бы ClusterSummaryBuilder (без кэша и LLM)."""
    from summary_branch.application.binder import DefaultEvidenceBinder
    from summary_branch.application.confidence import DefaultContentScorer
    from summary_branch.domain.models import ContentKey, ClusterSummary
    from summary_branch.domain.scoring import lead_from_news
    draft = draft or make_draft()
    claims, sources = DefaultEvidenceBinder().bind(draft, req)
    signals = DefaultContentScorer().score(req, draft, claims, sources)
    key = ContentKey(cluster_id=req.news[0].cluster_id, prompt_version="v1")
    return ClusterSummary(key=key, model_id="test", headline=draft.headline, for_you_template=draft.for_you_template,
                          for_you_fallback=draft.for_you_fallback, topic=draft.topic, severity=draft.severity,
                          claims=claims, sources=sources, lead=lead_from_news(req.news[0]), signals=signals)
