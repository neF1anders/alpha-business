"""Загрузка и проверка синтетического датасета (eval/data/*.jsonl)."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path


from summary_branch.domain.models import (  # noqa: E402
    BankMaterialRef, CompanyContext, RankedNewsItem, SourceKind, utcnow,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "eval" / "data"
TAG_VOCAB = {"rate", "fx", "labor", "demand", "credit", "export", "import", "metal", "agro", "retail", "it",
             "logistics", "tax", "inflation"}
TOPICS = {"rate", "fx", "labor", "demand", "other"}


@dataclass(frozen=True)
class NewsRow:
    item: RankedNewsItem
    topic: str
    key_facts: list[str]
    shelf_life_h: int


@dataclass(frozen=True)
class CompanyRow:
    company: CompanyContext
    name: str
    exposure: list[str]


def read_jsonl(name: str) -> list[dict]:
    path = DATA_DIR / name
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{name}:{number}: невалидный JSON: {exc}") from exc
    return rows


def load_news(rerank_score: float = 0.8) -> list[NewsRow]:
    now = utcnow()
    return [NewsRow(
        item=RankedNewsItem(
            news_id=r["news_id"], cluster_id=r["cluster_id"], title=r["title"], text=r["text"],
            source_kind=SourceKind(r["source_kind"]), url=r["url"],
            published_at=now - timedelta(hours=r["hours_ago"]), tags=r["tags"], rerank_score=rerank_score),
        topic=r["topic"], key_facts=r["key_facts"], shelf_life_h=r["shelf_life_h"]) for r in read_jsonl("news.jsonl")]


def load_bank() -> list[BankMaterialRef]:
    return [BankMaterialRef(doc_id=r["doc_id"], title=r["title"], page=r["page"], claim_text=r["claim_text"],
                            url=r["url"]) for r in read_jsonl("bank.jsonl")]


def load_bank_topics() -> dict[str, str]:
    return {r["doc_id"]: r["topic"] for r in read_jsonl("bank.jsonl")}


def load_companies() -> list[CompanyRow]:
    return [CompanyRow(
        company=CompanyContext(company_id=r["company_id"], segment_id=r["segment_id"], role=r["role"],
                               tags=r["tags"], features=r["features"]),
        name=r["name"], exposure=r["exposure"]) for r in read_jsonl("companies.jsonl")]


def _profile_problems(news_ids: set[str]) -> list[str]:
    """Профили компаний и переразмеченные пары (если файлы есть): схема, полнота сетки, допустимые метки."""
    from summary_branch.domain.models import CompanyProfile
    found: list[str] = []
    try:
        profiles = read_jsonl("profiles.jsonl")
    except OSError:
        return found
    ids = []
    for r in profiles:
        ids.append(r["company_id"])
        try:
            CompanyProfile(**r["profile"])
        except Exception as exc:
            found.append(f"{r['company_id']}: профиль: {exc}")
    for name in ("pairs_v2_a.jsonl", "pairs_v2_b.jsonl"):
        try:
            rows = read_jsonl(name)
        except OSError:
            continue
        seen = {(r["news_id"], r["company_id"]) for r in rows}
        found += [f"{name}: повтор пары" for _ in range(len(rows) - len(seen))]
        found += [f"{name}: метка {r['relevance']!r}" for r in rows if r.get("relevance") not in (0, 1, 2)]
        found += [f"{name}: неизвестная новость или компания {r['news_id']}/{r['company_id']}"
                  for r in rows if r["news_id"] not in news_ids or r["company_id"] not in ids]
        owners = {r["company_id"] for r in rows}
        if len(seen) != len(news_ids) * len(owners):
            found.append(f"{name}: пар {len(seen)}, ожидалось {len(news_ids) * len(owners)}")
    return found


def problems() -> list[str]:
    """Список найденных проблем; пустой список означает, что датасет годен."""
    found: list[str] = []
    try:
        raw_news, raw_bank, raw_companies = read_jsonl("news.jsonl"), read_jsonl("bank.jsonl"), read_jsonl("companies.jsonl")
    except (OSError, ValueError) as exc:
        return [str(exc)]
    for r in raw_news:
        nid = r.get("news_id", "?")
        if not set(r.get("tags", [])) <= TAG_VOCAB:
            found.append(f"{nid}: теги вне словаря {sorted(set(r.get('tags', [])) - TAG_VOCAB)}")
        if r.get("topic") not in TOPICS:
            found.append(f"{nid}: topic {r.get('topic')!r}")
        for fact in r.get("key_facts", []):
            if fact not in r.get("text", ""):
                found.append(f"{nid}: факт {fact!r} не найден в тексте дословно")
        if not r.get("key_facts"):
            found.append(f"{nid}: нет key_facts")
        if not isinstance(r.get("shelf_life_h"), int) or r["shelf_life_h"] <= 0:
            found.append(f"{nid}: shelf_life_h")
    for name, rows, key in (("news", raw_news, "news_id"), ("bank", raw_bank, "doc_id"),
                            ("companies", raw_companies, "company_id")):
        ids = [r.get(key) for r in rows]
        if len(ids) != len(set(ids)):
            found.append(f"{name}: повторяющиеся {key}")
    for r in raw_companies:
        cid = r.get("company_id", "?")
        if not set(r.get("tags", [])) <= TAG_VOCAB:
            found.append(f"{cid}: теги вне словаря")
        if not set(r.get("exposure", [])) <= {"rate", "fx", "labor", "demand"} or not r.get("exposure"):
            found.append(f"{cid}: exposure {r.get('exposure')!r}")
    found += _profile_problems({r["news_id"] for r in raw_news})
    by_cluster: dict[str, set[str]] = {}
    for r in raw_news:
        by_cluster.setdefault(r["cluster_id"], set()).add(r["topic"])
    found += [f"кластер {c}: разные topic {sorted(t)}" for c, t in by_cluster.items() if len(t) > 1]
    if not found:
        try:
            load_news(), load_bank(), load_companies()
        except Exception as exc:   # проверка схемы моделей ветки
            found.append(f"схема моделей: {exc}")
    return found


def summary() -> str:
    news, companies = load_news(), load_companies()
    topics: dict[str, int] = {}
    for n in news:
        topics[n.topic] = topics.get(n.topic, 0) + 1
    segments = sorted({c.company.segment_id for c in companies})
    return (f"новостей {len(news)} (темы {topics}), кластеров {len({n.item.cluster_id for n in news})}, "
            f"материалов банка {len(load_bank())}, компаний {len(companies)}, сегментов {len(segments)}: {segments}")
