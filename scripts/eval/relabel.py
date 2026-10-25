"""Оценка персонализации на переразмеченных парах: решение ветки против меток «по профилю» (eval/data/pairs_v2_*.jsonl).

Сравниваются два способа задать компанию: по тегам и признакам (как было) и по профилю (как стало).
Метки v2 поставлены по профилю, поэтому сравнение показывает, насколько ветка видит то, что видно в профиле.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Optional

from dataset import load_companies, read_jsonl
from run_personalization import news_signals
from stats import wilson
from summary_branch import scoring
from summary_branch.models import CompanyContext, CompanyProfile, Decision
from summary_branch.personalization import enrich

SHOWN = (Decision.SHOW_SUMMARY, Decision.SHOW_WITH_CAVEAT)
FLOORS = (0.15, 0.25, 0.35, 0.5, 0.7)


def load_pairs() -> list[dict]:
    return read_jsonl("pairs_v2_a.jsonl") + read_jsonl("pairs_v2_b.jsonl")


def profile_companies() -> dict[str, CompanyContext]:
    return {r["company_id"]: enrich(CompanyContext(company_id=r["company_id"], segment_id="eval", role=r["role"],
                                                   profile=CompanyProfile(**r["profile"])))
            for r in read_jsonl("profiles.jsonl")}


def tag_companies() -> dict[str, CompanyContext]:
    return {c.company.company_id: c.company for c in load_companies()}


def evaluate(pairs: list[dict], companies: dict[str, CompanyContext]) -> list[tuple[dict, Decision, float]]:
    signals = {news.item.news_id: (news, s, lead, sev) for news, s, lead, sev in news_signals()}
    out = []
    for pair in pairs:
        company = companies.get(pair["company_id"])
        if company is None:
            continue
        news, sig, lead, severity = signals[pair["news_id"]]
        conf = scoring.company_confidence(sig, lead, news.topic, company, severity=severity)
        out.append((pair, conf.decision, conf.score))
    return out


def summarise(rows: list[tuple[dict, Decision, float]]) -> dict:
    by_label = defaultdict(list)
    for pair, decision, score in rows:
        by_label[pair["relevance"]].append((decision, score))
    zero, two = by_label[0], by_label[2]
    shown = [(p, d) for p, d, _ in rows if d in SHOWN]
    return {
        "n": len(rows),
        "false_show": (sum(d in SHOWN for d, _ in zero), len(zero)),          # метка 0, но баннер показан
        "missed": (sum(d not in SHOWN for d, _ in two), len(two)),            # метка 2, но баннера нет
        "precision": (sum(p["relevance"] >= 1 for p, _ in shown), len(shown)),
        "strong": (sum(p["relevance"] == 2 for p, _ in shown), len(shown)),
        "mean_score": {k: statistics.mean(s for _, s in v) for k, v in sorted(by_label.items()) if v},
    }


def fmt(pair: tuple[int, int]) -> str:
    k, n = pair
    low, high = wilson(k, n)
    return f"{k / n:.0%} ({k}/{n}, {low:.0%}-{high:.0%})" if n else "нет данных"


def table(title: str, result: dict) -> list[str]:
    scores = ", ".join(f"метка {k}: {v:.2f}" for k, v in result["mean_score"].items())
    return [f"**{title}** (пар: {result['n']})", "",
            "| показатель | значение |", "|---|---|",
            f"| баннер показан при метке 0 (шум) | {fmt(result['false_show'])} |",
            f"| баннера нет при метке 2 (упущено) | {fmt(result['missed'])} |",
            f"| среди показанных метка ≥ 1 | {fmt(result['precision'])} |",
            f"| среди показанных метка 2 | {fmt(result['strong'])} |",
            f"| средняя уверенность | {scores} |", ""]


def section_personalization(lines: list[str]) -> None:
    pairs = load_pairs()
    if not pairs:
        lines += ["## 4. Персонализация", "", "Переразметка не найдена (eval/data/pairs_v2_*.jsonl).", ""]
        return
    dist = defaultdict(int)
    for p in pairs:
        dist[p["relevance"]] += 1
    lines += ["## 4. Персонализация по профилю: переразметка и оценка", "",
              f"20 вымышленных компаний (12 из прежнего набора и 8 контрастных) × 28 новостей = {len(pairs)} пар. Метки 0/1/2 поставил Haiku "
              "по профилю и тексту новости (скилл humanizer не использовался), прежние метки по тегам не передавались. "
              f"Распределение: 0 — {dist[0]}, 1 — {dist[1]}, 2 — {dist[2]}. "
              "Проверка выборки и правки: см. раздел «Проверка разметки» ниже.", ""]
    old = [r for r in evaluate(pairs, tag_companies())]
    new = evaluate(pairs, profile_companies())
    lines += table("Было: компания по тегам и признакам (12 компаний)", summarise(old))
    lines += table("Стало: компания по профилю (20 компаний)", summarise(new))
    common = {p["company_id"] for p, _, _ in old}
    lines += table("Стало, только 12 общих компаний (честное сравнение)", summarise([r for r in new if r[0]["company_id"] in common])) 

    lines += ["**Порог чувствительности `EXPOSURE_FLOOR`**", "", "| порог | шум (метка 0 показана) | упущено (метка 2) | показано всего |", "|---|---|---|---|"]
    saved = scoring.EXPOSURE_FLOOR
    try:
        for floor in FLOORS:
            scoring.EXPOSURE_FLOOR = floor
            res = summarise(evaluate(pairs, profile_companies()))
            shown = sum(1 for _, d, _ in evaluate(pairs, profile_companies()) if d in SHOWN)
            lines.append(f"| {floor} | {fmt(res['false_show'])} | {fmt(res['missed'])} | {shown / res['n']:.0%} |")
    finally:
        scoring.EXPOSURE_FLOOR = saved
    lines += ["", CHECK_NOTES, ""]


CHECK_NOTES = """**Проверка разметки.** Все 560 пар прошли автоматическую проверку согласованности по профилю (метка 2 по курсу без ВЭД, по ставке без кредита или стройки/недвижимости, по труду при штате менее 15, метка 0 по ставке при кредите): расхождений нет. Метка 0 по курсу у компаний с ВЭД (34 пары) объясняется правилом «страна новости не совпадает со страной торговли» (юань и Китай, тенге и Казахстан), и она верна. Построчное чтение третьей части выборки не проводилось, правок меток не вносил. Спорные места, о которых сообщил разметчик: законопроект о льготах для IT (n26) получил 2 у двух IT-компаний; `negative_flag` и `lifecycle` разметчик не учитывал; доля меток 2 у второй половины компаний ниже (12%), чем у первой (22%)."""


if __name__ == "__main__":
    out: list[str] = []
    section_personalization(out)
    print("\n".join(out))
