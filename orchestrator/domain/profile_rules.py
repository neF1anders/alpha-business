"""Правила, выводимые из тегов компании: сегмент и человекочитаемые признаки.

Теги строит scripts/build_tags.py, словарь описан в docs/tags.md.
"""
from __future__ import annotations

from typing import Iterable

UNKNOWN = "unknown"

SECTOR_RU = {
    "agro": "сельское хозяйство", "mining": "добыча полезных ископаемых", "manufacturing": "производство",
    "energy": "энергетика", "utilities": "коммунальные услуги", "construction": "строительство",
    "trade": "торговля", "transport": "транспорт и логистика", "horeca": "общепит и гостиничный бизнес",
    "it_telecom": "IT и связь", "finance": "финансовый сектор", "real_estate": "недвижимость",
    "prof_services": "профессиональные услуги", "admin_services": "административные услуги",
    "public": "госсектор", "education": "образование", "health": "здравоохранение",
    "culture_sport": "культура и спорт", "other_services": "прочие услуги",
}
PRODUCT_RU = {
    "credit": "кредитные продукты", "leasing": "лизинг", "factoring": "факторинг",
    "deposit": "депозиты", "guarantee": "банковские гарантии",
}
SIZES = ("large", "mid", "small")


def _values(tags: Iterable[str], namespace: str) -> list[str]:
    prefix = namespace + ":"
    return sorted(t[len(prefix):] for t in tags if t.startswith(prefix))


def segment_of(tags: Iterable[str]) -> str:
    """Сегмент = отрасль × размер, например `trade-mid`. Размер неизвестен -> `unknown`."""
    tags = list(tags)
    sector = next(iter(_values(tags, "sector")), UNKNOWN)
    size = next((s for s in _values(tags, "size") if s in SIZES), UNKNOWN)
    return f"{sector}-{size}"


def features_of(tags: Iterable[str]) -> tuple[str, ...]:
    """Короткие признаки для строки «для вас». Только то, что подтверждено тегами."""
    tags = list(tags)
    out: list[str] = []
    sector = next(iter(_values(tags, "sector")), None)
    if sector in SECTOR_RU:
        out.append(f"отрасль: {SECTOR_RU[sector]}")
    products = [PRODUCT_RU[p] for p in _values(tags, "product") if p in PRODUCT_RU]
    if products:
        out.append("продукты банка: " + ", ".join(products))
    if "factor:fx" in tags:
        out.append("есть внешнеторговые операции")
    if "factor:commodities" in tags:
        out.append("зависимость от цен на сырьё")
    regions = _values(tags, "region")
    if regions:
        out.append(f"регион: {regions[0]}")
    return tuple(out)
