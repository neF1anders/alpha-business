"""Общие фабрики тестовых данных."""
from datetime import timedelta

from summary_branch.models import (
    BankMaterialRef, CompanyContext, Mode, RankedNewsItem, SourceKind, SummaryRequest, utcnow,
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
