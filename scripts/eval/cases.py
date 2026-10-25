"""Сборка запросов и черновиков (честных и испорченных) для оффлайн-прогона валидатора. Без сети и без LLM."""
from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass
from typing import Callable, Optional

from dataset import CompanyRow, NewsRow, load_bank, load_bank_topics, load_companies, load_news
from summary_branch.config import BranchConfig
from summary_branch.fakes import FakeLLMClient
from summary_branch.models import BankMaterialRef, Mode, SummaryRequest
from summary_branch.scoring import tag_overlap
from summary_branch.summarizer import build_prompt

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_ANTONYMS = (("сохранил", "повысил"), ("повысил", "снизил"), ("снизил", "повысил"), ("вырос", "снизился"),
             ("выросла", "снизилась"), ("замедлился", "ускорился"), ("замедлилась", "ускорилась"),
             ("ускорился", "замедлился"), ("снизился", "вырос"), ("сократил", "увеличил"), ("увеличил", "сократил"))


@dataclass(frozen=True)
class DraftCase:
    case_id: str
    kind: str                 # honest | good_trim | число_подменено | ...
    is_valid: bool            # истинная метка: честный ли черновик
    request: SummaryRequest
    draft: dict


def best_company(news: NewsRow, companies: list[CompanyRow]) -> CompanyRow:
    return max(companies, key=lambda c: (tag_overlap(c.company.tags, news.item.tags), c.company.company_id))


def banks_for(news: NewsRow, bank: list[BankMaterialRef], topics: dict[str, str]) -> list[BankMaterialRef]:
    return [b for b in bank if topics.get(b.doc_id) == news.topic][:1]   # один тезис банка по теме новости


def honest_draft(req: SummaryRequest) -> dict:
    return FakeLLMClient()._draft_from_prompt(build_prompt(req, BranchConfig()))


def _shift_number(text: str) -> str:
    def bump(match: re.Match) -> str:
        value = float(match.group().replace(",", "."))
        shifted = round(value * 1.17 + 1, 1)
        return str(shifted).replace(".", ",") if "," in match.group() or shifted % 1 else str(int(shifted))
    return _NUM.sub(bump, text, count=1)


def _swap_number(draft: dict, req: SummaryRequest, rng: random.Random) -> Optional[dict]:
    claim = next((c for c in draft["claims"] if c["type"] == "fact" and _NUM.search(c["text"])), None)
    if claim is None:
        return None
    claim["text"] = _shift_number(claim["text"])
    return draft


def _extra_number(draft: dict, req: SummaryRequest, rng: random.Random) -> Optional[dict]:
    claim = next(c for c in draft["claims"] if c["type"] == "fact")
    claim["text"] += " и за год показатель вырос на 7,3%"
    return draft


def _antonym(draft: dict, req: SummaryRequest, rng: random.Random) -> Optional[dict]:
    for claim in draft["claims"]:
        if claim["type"] != "fact":
            continue
        for src, dst in _ANTONYMS:
            if src in claim["text"]:
                claim["text"] = claim["text"].replace(src, dst, 1)
                return draft
    return None


def _foreign_fact(draft: dict, req: SummaryRequest, rng: random.Random) -> Optional[dict]:
    foreign = FOREIGN_SENTENCES.get(req.news[0].news_id)
    if not foreign:
        return None
    next(c for c in draft["claims"] if c["type"] == "fact")["text"] = foreign
    return draft


def _invented_entity(draft: dict, req: SummaryRequest, rng: random.Random) -> Optional[dict]:
    next(c for c in draft["claims"] if c["type"] == "fact")["text"] = (
        "Компания «Вектор-Альфа» объявила о слиянии с крупным конкурентом по отрасли")
    return draft


def _unsupported_model(draft: dict, req: SummaryRequest, rng: random.Random) -> Optional[dict]:
    for text in ("Рынок вскоре перейдёт к новому циклу", "Конкуренты, вероятно, пересмотрят цены",
                 "Регуляторы могут ужесточить правила"):
        draft["claims"].append({"type": "hypothesis", "text": text, "evidence_ids": []})
    return draft


def _no_fact(draft: dict, req: SummaryRequest, rng: random.Random) -> Optional[dict]:
    draft["claims"] = [c for c in draft["claims"] if c["type"] not in ("fact", "bank")]
    return draft


def _good_trim(draft: dict, req: SummaryRequest, rng: random.Random) -> Optional[dict]:
    claim = next(c for c in draft["claims"] if c["type"] == "fact")
    claim["text"] = claim["text"].split(", ")[0].split(" — ")[0]
    return draft


# Чужие предложения для подмены: заполняется в build_cases из других тем.
FOREIGN_SENTENCES: dict[str, str] = {}

PERTURBATIONS: dict[str, tuple[bool, Callable]] = {
    "honest": (True, lambda d, r, g: d),
    "good_trim": (True, _good_trim),
    "число_подменено": (False, _swap_number),
    "число_добавлено": (False, _extra_number),
    "смысл_наоборот": (False, _antonym),
    "чужой_факт": (False, _foreign_fact),
    "выдуманная_сущность": (False, _invented_entity),
    "выводы_без_опоры": (False, _unsupported_model),
    "нет_фактов": (False, _no_fact),
}


def build_cases(seed: int = 7) -> list[DraftCase]:
    rng = random.Random(seed)
    news_rows, companies, bank = load_news(), load_companies(), load_bank()
    topic_of_doc = load_bank_topics()
    for row in news_rows:      # чужое предложение: первое предложение новости другой темы
        other = rng.choice([n for n in news_rows if n.topic != row.topic and n.item.cluster_id != row.item.cluster_id])
        FOREIGN_SENTENCES[row.item.news_id] = re.split(r"(?<=[.!?])\s+", other.item.text)[0].rstrip(".")
    cases: list[DraftCase] = []
    seen_clusters: set[str] = set()
    for row in news_rows:
        if row.item.cluster_id in seen_clusters:
            continue
        seen_clusters.add(row.item.cluster_id)
        cluster = [n.item for n in news_rows if n.item.cluster_id == row.item.cluster_id]
        company = best_company(row, companies)
        req = SummaryRequest(trace_id=f"off-{row.item.news_id}", mode=Mode.BANNER, company=company.company,
                             news=cluster, bank_materials=banks_for(row, bank, topic_of_doc))
        base = honest_draft(req)
        for kind, (valid, fn) in PERTURBATIONS.items():
            draft = fn(copy.deepcopy(base), req, rng)
            if draft is not None:
                cases.append(DraftCase(f"{row.item.news_id}:{kind}", kind, valid, req, draft))
    return cases
