"""Профиль компании -> чувствительность к темам, сегмент, теги и признаки «для вас». Без LLM и ввода-вывода.

Правила стартовые и экспертные: меток реакции пользователей пока нет, значения проверять по `mark_seen` и `suppress`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import CompanyContext, CompanyProfile, Product, Relationship

TOPICS = ("rate", "fx", "labor", "demand")
SENSITIVE = 0.5                  # с этого значения компания считается чувствительной к теме
INDIRECT_WEIGHT = 0.5            # косвенная связь через контрагентов весит вдвое меньше прямой
MAX_TRADE_COUNTRIES = 3
MAX_SECTOR_NAME = 60

_DEBT_PRODUCTS = {Product.LOAN, Product.LEASING, Product.FACTORING}
_RATE_SENSITIVE_DIVISIONS = {"41", "42", "43", "68"}      # стройка и недвижимость зависят от ставки через спрос
_WHOLESALE_DIVISION = "46"
_CONSUMER_DIVISIONS = {"47", "55", "56", "85", "86", "93", "96"}
_LARGE_STAFF = {"101-250", "Более 251"}
_MEDIUM_STAFF = {"16-100"}
_STAFF_LABELS = {"Менее 15": "менее 15", "16-100": "16-100", "101-250": "101-250", "Более 251": "более 251"}
_NEWS_SECTOR_PREFIX = "okved:"
# словарные теги отраслей новости -> разделы ОКВЭД (теги `okved:NN` задают раздел напрямую)
_SECTOR_TAGS = {"metal": {"24", "25"}, "agro": {"01", "10"}, "retail": {"47"}, "it": {"62", "63"},
                "logistics": {"49", "52", "53"}}


@dataclass(frozen=True)
class Exposure:
    value: float
    reasons: tuple[str, ...] = ()


def okved_division(profile: CompanyProfile) -> str:
    return profile.okved_code.partition(".")[0]


def _rate(profile: CompanyProfile) -> Exposure:
    # продукты видны только у клиентов: у остальных «есть кредит» не знаем, поэтому не используем
    held = profile.products if profile.relationship == Relationship.CLIENT else set()
    if held & _DEBT_PRODUCTS:
        return Exposure(1.0, ("есть кредитный продукт банка",))
    if okved_division(profile) in _RATE_SENSITIVE_DIVISIONS:
        return Exposure(0.6, ("отрасль зависит от стоимости денег",))
    if Product.DEPOSIT in held:
        return Exposure(0.6, ("есть депозит",))
    return Exposure(0.2)


def _fx(profile: CompanyProfile) -> Exposure:
    if profile.foreign_trade:
        return Exposure(1.0, ("внешнеторговая деятельность",))
    if okved_division(profile) == _WHOLESALE_DIVISION:
        return Exposure(0.4, ("оптовая торговля",))
    return Exposure(0.1)


def _labor(profile: CompanyProfile) -> Exposure:
    if profile.workers_band in _LARGE_STAFF:
        return Exposure(1.0, ("большой штат",))
    if profile.workers_band in _MEDIUM_STAFF and Product.PAYROLL in profile.products:
        return Exposure(0.6, ("средний штат и зарплатный проект",))
    return Exposure(0.3)


def _demand(profile: CompanyProfile) -> Exposure:
    if okved_division(profile) in _CONSUMER_DIVISIONS:
        return Exposure(0.8, ("работа с потребительским спросом",))
    return Exposure(0.4)


_RULES = {"rate": _rate, "fx": _fx, "labor": _labor, "demand": _demand}


def topic_exposure(profile: CompanyProfile) -> dict[str, Exposure]:
    return {topic: rule(profile) for topic, rule in _RULES.items()}


def news_sectors(news_tags: Iterable[str]) -> set[str]:
    """Разделы ОКВЭД, о которых новость: теги вида `okved:46` и отраслевые слова (`metal`, `retail`…)."""
    sectors: set[str] = set()
    for tag in news_tags:
        if tag.startswith(_NEWS_SECTOR_PREFIX):
            sectors.add(tag[len(_NEWS_SECTOR_PREFIX):])
        else:
            sectors |= _SECTOR_TAGS.get(tag.lower(), set())
    return sectors


def sector_match(profile: CompanyProfile, sectors: set[str]) -> bool:
    return okved_division(profile) in sectors


def indirect_exposure(profile: CompanyProfile, sectors: set[str]) -> float:
    """Доля контрагентов из отраслей новости, умноженная на INDIRECT_WEIGHT."""
    if not profile.payments or not sectors:
        return 0.0
    share = sum(v for k, v in profile.payments.counterparty_sectors.items() if k in sectors)
    return min(share, 1.0) * INDIRECT_WEIGHT


def segment_signature(profile: CompanyProfile) -> str:
    """Рекомендуемый `segment_id`: какие темы важны компании и кто она для банка. Значения разные, кэш общий."""
    bits = "".join(topic[0] if exposure.value >= SENSITIVE else "-" for topic, exposure in topic_exposure(profile).items())
    return f"{bits}-{profile.relationship.value}"


def derive_tags(profile: CompanyProfile) -> list[str]:
    tags = [topic for topic, exposure in topic_exposure(profile).items() if exposure.value >= SENSITIVE]
    if division := okved_division(profile):
        tags.append(f"{_NEWS_SECTOR_PREFIX}{division}")
    if profile.region:
        tags.append(f"region:{profile.region.lower()}")
    tags.append(f"relationship:{profile.relationship.value}")
    return tags


def derive_features(profile: CompanyProfile) -> list[str]:
    """Человекочитаемые факты профиля для строки «для вас». Суммы и контрагенты сюда не попадают."""
    features: list[str] = []
    if profile.foreign_trade:
        countries = ", ".join(c.capitalize() for c in profile.trade_countries[:MAX_TRADE_COUNTRIES])
        features.append("внешнеторговая деятельность" + (f": {countries}" if countries else ""))
    if profile.relationship == Relationship.CLIENT:
        features.extend(_PRODUCT_FEATURES[p] for p in _PRODUCT_ORDER if p in profile.products)
    if profile.workers_band in _STAFF_LABELS:
        features.append(f"штат {_STAFF_LABELS[profile.workers_band]} человек")
    if profile.okved_name:
        features.append(f"отрасль: {profile.okved_name[:MAX_SECTOR_NAME]}")
    if profile.region:
        features.append(f"регион: {profile.region}")
    return features


_PRODUCT_ORDER = (Product.LOAN, Product.LEASING, Product.FACTORING, Product.GUARANTEE, Product.DEPOSIT, Product.PAYROLL)
_PRODUCT_FEATURES = {
    Product.LOAN: "действующий кредит", Product.LEASING: "действующий лизинг", Product.FACTORING: "факторинг",
    Product.GUARANTEE: "банковская гарантия", Product.DEPOSIT: "депозит", Product.PAYROLL: "зарплатный проект",
}


def enrich(company: CompanyContext) -> CompanyContext:
    """Добавляет к тегам и признакам, пришедшим от оркестратора, выведенные из профиля. Без профиля возвращает как есть."""
    if company.profile is None:
        return company
    tags = list(dict.fromkeys([*company.tags, *derive_tags(company.profile)]))
    features = list(dict.fromkeys([*company.features, *derive_features(company.profile)]))
    return company.model_copy(update={"tags": tags, "features": features})
