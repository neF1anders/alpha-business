"""Правила, выводимые из тегов компании: сегмент и человекочитаемые признаки.

Теги строит scripts/build_tags.py, словарь описан в docs/tags.md.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .models import CompanyDossier

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


# Диапазоны двузначного ОКВЭД -> тег `sector:*` (как в scripts/build_tags.py и docs/tags.md)
SECTOR_RANGES = (
    (1, 3, "agro"), (5, 9, "mining"), (10, 33, "manufacturing"), (35, 35, "energy"),
    (36, 39, "utilities"), (41, 43, "construction"), (45, 47, "trade"), (49, 53, "transport"),
    (55, 56, "horeca"), (58, 63, "it_telecom"), (64, 66, "finance"), (68, 68, "real_estate"),
    (69, 75, "prof_services"), (77, 82, "admin_services"), (84, 84, "public"), (85, 85, "education"),
    (86, 88, "health"), (90, 93, "culture_sport"), (94, 96, "other_services"),
)
COUNTERPARTY_MIN_SHARE = 0.1     # отрасль контрагентов, через которую идёт хотя бы десятая часть потока
COUNTERPARTY_SEARCH_LIMIT = 3


def sector_of_division(division: str) -> str | None:
    if not division.isdigit():
        return None
    code = int(division)
    return next((name for low, high, name in SECTOR_RANGES if low <= code <= high), None)


def counterparty_search_tags(dossier: "CompanyDossier") -> frozenset[str]:
    """Теги `sector:*` ключевых отраслей поставщиков и покупателей: по ним ищутся новости о нишах партнёров."""
    sides = sorted((*dossier.suppliers, *dossier.buyers), key=lambda s: s.share, reverse=True)
    tags: list[str] = []
    for side in sides:
        sector = sector_of_division(side.division)
        if side.share >= COUNTERPARTY_MIN_SHARE and sector and f"sector:{sector}" not in tags:
            tags.append(f"sector:{sector}")
    return frozenset(tags[:COUNTERPARTY_SEARCH_LIMIT])
