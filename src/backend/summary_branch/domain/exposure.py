"""Пути влияния новости на компанию: карта влияния кластера x профиль и платёжный граф. Без LLM и ввода-вывода.

Каждый путь — объяснимое основание с весом 0..1. Итоговая персональная чувствительность — «шумное ИЛИ» весов:
несколько слабых связей вместе значат больше одной, но не больше единицы. Пути через контрагентов помечены
как гипотеза: платёж показывает связь, но не её характер.
"""
from __future__ import annotations

from typing import Iterable

from .models import (
    ClusterSummary, CompanyContext, CompanyProfile, CounterpartySector, ExposureKind, ExposurePath, SectorImpact,
)
from .personalization import SENSITIVE, news_sectors, okved_division, topic_exposure

MAX_PATHS = 5
MIN_COUNTERPARTY_SHARE = 0.05      # меньшая доля потока не делает отрасль контрагентов заметной для компании
OWN_SECTOR_WEIGHT = 1.0
FOREIGN_TRADE_WEIGHT = 0.9
REGION_WEIGHT = 0.5
AFFILIATE_WEIGHT = 0.35
COUNTERPARTY_BASE, COUNTERPARTY_SLOPE = 0.25, 1.5   # 5% потока -> 0.33, 30% -> 0.7, от 50% -> 1.0
DOMINANT_SHARE = 0.5

TOPIC_NAMES = {"rate": "ключевая ставка", "fx": "курс валют", "labor": "рынок труда",
               "demand": "потребительский спрос"}
_PRODUCT_REASONS = ("кредитный продукт", "депозит")

# Названия отраслей по диапазонам двузначного ОКВЭД (как теги `sector:*`, docs/tags.md)
_SECTOR_RANGES = (
    (1, 3, "сельское хозяйство"), (5, 9, "добыча полезных ископаемых"), (10, 33, "обрабатывающие производства"),
    (35, 35, "энергетика"), (36, 39, "водоснабжение и отходы"), (41, 43, "строительство"), (45, 47, "торговля"),
    (49, 53, "транспорт и логистика"), (55, 56, "гостиницы и общепит"), (58, 63, "IT и связь"),
    (64, 66, "финансы"), (68, 68, "недвижимость"), (69, 75, "профессиональные услуги"),
    (77, 82, "административные услуги"), (84, 84, "госуправление"), (85, 85, "образование"),
    (86, 88, "здравоохранение"), (90, 93, "культура и спорт"), (94, 96, "прочие услуги"),
)


# Страницы действий интернет-банка, где путь влияния уместен как плашка рядом с объектом
_PLACEMENTS = {
    ExposureKind.SUPPLIERS: ("payments", "new_payment"),
    ExposureKind.BUYERS: ("payments", "counterparties"),
    ExposureKind.AFFILIATES: ("counterparties",),
    ExposureKind.PRODUCT: ("payments",),
}


def placements_for(paths: list[ExposurePath]) -> list[str]:
    return list(dict.fromkeys(page for p in paths for page in _PLACEMENTS.get(p.kind, ())))


def sector_name(division: str) -> str:
    if not division.isdigit():
        return division
    code = int(division)
    return next((name for low, high, name in _SECTOR_RANGES if low <= code <= high), f"ОКВЭД {division}")


def noisy_or(weights: Iterable[float]) -> float:
    miss = 1.0
    for weight in weights:
        miss *= 1.0 - max(0.0, min(weight, 1.0))
    return 1.0 - miss


def personal_exposure(paths: list[ExposurePath]) -> float:
    return noisy_or(p.weight for p in paths)


def indirect_weight(paths: list[ExposurePath]) -> float:
    """Связь через контрагентов и связанные компании, без прямых оснований."""
    return noisy_or(p.weight for p in paths if p.kind in (ExposureKind.SUPPLIERS, ExposureKind.BUYERS,
                                                          ExposureKind.AFFILIATES))


def exposure_paths(summary: ClusterSummary, company: CompanyContext) -> list[ExposurePath]:
    """Основания, почему новость кластера касается компании, по убыванию веса. Без профиля — пусто."""
    impacts: dict[str, SectorImpact | None] = {impact.division: impact for impact in summary.impacts}
    if not impacts:   # модель карту не дала: отрасли из тегов главной новости, без механизма
        impacts = {division: None for division in news_sectors(summary.lead.tags)}
    return _collect(company, impacts, summary.topic, summary.countries, summary.regions)


def news_exposure_paths(company: CompanyContext, news_tags: list[str]) -> list[ExposurePath]:
    """Пути без карты влияния (кластер ещё не разобран моделью): только отраслевые теги новости."""
    return _collect(company, {division: None for division in news_sectors(news_tags)}, "", [], [])


def _collect(company: CompanyContext, impacts: dict[str, SectorImpact | None], topic: str, countries: list[str],
             regions: list[str]) -> list[ExposurePath]:
    profile = company.profile
    if profile is None:
        return []
    paths = [*_own_sector(profile, impacts), *_macro(profile, topic), *_foreign_trade(profile, countries),
             *_region(profile, regions), *_counterparties(profile, impacts), *_affiliates(profile, impacts)]
    paths.sort(key=lambda p: p.weight, reverse=True)
    return [p.model_copy(update={"path_id": f"p{i}"}) for i, p in enumerate(paths[:MAX_PATHS], start=1)]


def _path(kind: ExposureKind, weight: float, explanation: str, impact: SectorImpact | None = None,
          hypothesis: bool = False) -> ExposurePath:
    return ExposurePath(path_id="", kind=kind, weight=round(min(max(weight, 0.0), 1.0), 3), explanation=explanation,
                        mechanism=impact.mechanism if impact else "", hypothesis=hypothesis)


def _own_sector(profile: CompanyProfile, impacts: dict[str, SectorImpact | None]) -> list[ExposurePath]:
    division = okved_division(profile)
    if division not in impacts:
        return []
    name = profile.okved_name or sector_name(division)
    return [_path(ExposureKind.OWN_SECTOR, OWN_SECTOR_WEIGHT, f"ваша отрасль: {name}", impacts[division])]


def _macro(profile: CompanyProfile, topic: str) -> list[ExposurePath]:
    exposure = topic_exposure(profile).get(topic)
    if exposure is None or exposure.value < SENSITIVE or not exposure.reasons:
        return []
    reason = exposure.reasons[0]
    kind = ExposureKind.PRODUCT if any(r in reason for r in _PRODUCT_REASONS) else ExposureKind.MACRO
    return [_path(kind, exposure.value, f"{TOPIC_NAMES.get(topic, topic)}: {reason}")]


def _foreign_trade(profile: CompanyProfile, countries: list[str]) -> list[ExposurePath]:
    shared = sorted({c.upper() for c in countries} & {c.upper() for c in profile.trade_countries})
    if not shared:
        return []
    countries = ", ".join(c.capitalize() for c in shared[:3])
    return [_path(ExposureKind.FOREIGN_TRADE, FOREIGN_TRADE_WEIGHT, f"внешнеторговые операции: {countries}")]


def _region(profile: CompanyProfile, regions: list[str]) -> list[ExposurePath]:
    if not profile.region:
        return []
    own = profile.region.casefold()
    if not any(r.casefold() in own or own in r.casefold() for r in regions if r):
        return []
    return [_path(ExposureKind.REGION, REGION_WEIGHT, f"ваш регион: {profile.region}")]


def _counterparties(profile: CompanyProfile, impacts: dict[str, SectorImpact | None]) -> list[ExposurePath]:
    if profile.payments is None:
        return []
    paths: list[ExposurePath] = []
    for kind, sectors, flow in ((ExposureKind.SUPPLIERS, profile.payments.suppliers, "исходящих"),
                                (ExposureKind.BUYERS, profile.payments.buyers, "входящих")):
        for sector in sectors:
            if sector.division in impacts and sector.share >= MIN_COUNTERPARTY_SHARE:
                paths.append(_path(kind, COUNTERPARTY_BASE + COUNTERPARTY_SLOPE * sector.share,
                                   _counterparty_explanation(kind, sector, flow), impacts[sector.division],
                                   hypothesis=True))
    return paths


def _counterparty_explanation(kind: ExposureKind, sector: CounterpartySector, flow: str) -> str:
    who = "поставщики" if kind == ExposureKind.SUPPLIERS else "покупатели"
    text = f"{who} из отрасли «{sector.name or sector_name(sector.division)}» — {round(sector.share * 100)}% {flow} платежей"
    if sector.share >= DOMINANT_SHARE:
        text += ", основная часть потока"
    return text


def _affiliates(profile: CompanyProfile, impacts: dict[str, SectorImpact | None]) -> list[ExposurePath]:
    own = okved_division(profile)
    return [_path(ExposureKind.AFFILIATES, AFFILIATE_WEIGHT,
                  f"связанные компании из отрасли «{sector_name(division)}»", impacts[division], hypothesis=True)
            for division in dict.fromkeys(profile.affiliate_sectors) if division in impacts and division != own]
