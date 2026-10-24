"""Сводит оффлайн-свип, живые прогоны и разметку в eval/results/REPORT.md и печатает рекомендованные значения.

uv run python scripts/eval/tune.py
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from dataset import load_companies, load_news, read_jsonl
from stats import percentile, rate
from summary_branch.scoring import tag_overlap

RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"
SLO_S = 4.0
TTL_CAP_H = 168   # моё ограничение: оценки сроков синтетические, неделя достаточно для самой долгой темы


def load(name: str) -> list[dict]:
    path = RESULTS / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def section_offline(lines: list[str]) -> None:
    data = json.loads((RESULTS / "offline.json").read_text(encoding="utf-8"))
    base = data["baseline"]
    lines += ["## 1. Валидатор: оффлайн-свип на испорченных черновиках", "",
              f"Случаев {data['cases']} (честных {base['good']}, испорченных {base['bad']}); только новости, где честный черновик "
              "доходит до «высокой» при текущих порогах (так измеряется валидатор, а не свежесть).", "",
              f"- Текущие пороги: плохое принято как «высокая» **{rate(base['fa_summary'], base['bad'])}**, "
              f"показано с пометкой или выше {rate(base['fa_shown'], base['bad'])}; честное не дошло до «высокой» {rate(base['fr_summary'], base['good'])}.", "",
              "Только пороги (600 комбинаций HIGH × MEDIUM × NUMERIC_MISS_GROUNDING × min_grounding): лучшая комбинация с ложным отказом "
              "честным не более 10% даёт ложное принятие:"]
    ok = [r for r in data["sweep"] if r["fr_summary"] <= 0.1 * r["good"]]
    best = min(ok, key=lambda r: (r["fa_summary"], r["fr_summary"]))
    lines.append(f"{rate(best['fa_summary'], best['bad'])} при {best['params']}.")
    lines += ["", "С дополнительными правилами (число должно быть в источнике; минимум общих слов с источником у каждого проверяемого утверждения):", "",
              "| HIGH | strict_numeric | min_claim_lex | плохое как «высокая» | плохое показано | честное не «высокая» | повторов на плохих |", "|---|---|---|---|---|---|---|"]
    for r in data["rules"]:
        p = r["params"]
        if p["HIGH"] in (0.75, 0.8):
            lines.append(f"| {p['HIGH']} | {p['strict_numeric']} | {p['min_claim_lex']} | {rate(r['fa_summary'], r['bad'])} | "
                         f"{r['fa_shown']}/{r['bad']} | {r['fr_summary']}/{r['good']} | {r['retry_bad']}/{r['bad']} |")
    lines += ["", "Порча по видам (текущие пороги): n / «высокая» / показано / ушло на повтор", ""]
    for kind, row in data["by_kind"].items():
        lines.append(f"- {kind}: {row['n']} / {row['summary']} / {row['shown']} / {row['invalid']}")
    lines.append("")


def chat_block(lines: list[str], title: str, rows: list[dict]) -> None:
    secs = [r["seconds"] for r in rows]
    lines += [f"### {title}: {len(rows)} запросов", "",
              f"- Задержка запрос-ответ: p50 {percentile(secs, 50):.2f} с, p90 {percentile(secs, 90):.2f}, p95 {percentile(secs, 95):.2f}, макс {max(secs):.2f}.",
              f"- Дольше SLO {SLO_S:.0f} с: {rate(sum(s > SLO_S for s in secs), len(secs))}.",
              f"- Ответ «только новость»: {rate(sum(r['raw_news'] for r in rows), len(rows))}; с пометкой: {sum(r['caveat'] for r in rows)}.",
              f"- Среднее число утверждений в ответе: {statistics.mean(len(r['all_claims']) for r in rows if not r['raw_news']):.1f}; "
              f"вызовов на запрос: {statistics.mean(r['calls'] for r in rows):.2f}."]
    for name, flag in (("по теме", True), ("не по теме", False)):
        part = [r for r in rows if r["on_topic"] == flag]
        lines.append(f"- {name}: «только новость» {rate(sum(r['raw_news'] for r in part), len(part))}.")
    lines.append("")


def section_live(lines: list[str]) -> None:
    chat, lean, banner = load("live_chat.jsonl"), load("live_chat_lean.jsonl"), load("live_banner.jsonl")
    lines += ["## 2. Живой прогон на шлюзе", "", "### Чат: что было и что получилось", ""]
    if chat:
        chat_block(lines, "Было: маршрут qwen-first, промпт D (3 утверждения, follow_ups до двух)", chat)
    if lean:
        chat_block(lines, "Стало: deepseek-v4.1-flash первой, qwen запасной, укороченный вывод (E)", lean)
    if banner:
        secs = [r["seconds"] for r in banner]
        decisions = defaultdict(int)
        for r in banner:
            decisions[r["decision"] or "нет баннера"] += 1
        lines += [f"### Баннеры: {len(banner)} кластеров, повторы до 2", "",
                  f"- Решения: {dict(decisions)}.",
                  f"- Вызовов LLM на кластер: среднее {statistics.mean(r['calls'] for r in banner):.2f}; доля кластеров с повтором: {rate(sum(r['calls'] > 1 for r in banner), len(banner))}.",
                  f"- Время на кластер (генерация): p50 {percentile(secs, 50):.1f} с, p95 {percentile(secs, 95):.1f} с.", ""]
    rows = banner + chat + lean
    claims = [c for r in rows for c in r["claims"]]
    if claims:
        lex = [c["lex"] for c in claims]
        miss = sum(bool(c["numbers_missing"]) for c in claims)
        lines += ["### Реальные утверждения fact/bank против правил-кандидатов", "",
                  f"- Утверждений: {len(claims)}; с числом, которого нет в источнике: {rate(miss, len(claims))}.",
                  "- Доля общих основ слов с источником (lex): " + ", ".join(f"p{q} {percentile(lex, q):.2f}" for q in (5, 10, 25, 50)) + ".",
                  "- Доля утверждений ниже порога lex: " + ", ".join(
                      f"{t}: {sum(x < t for x in lex) / len(lex):.1%}" for t in (0.3, 0.4, 0.5, 0.6, 0.7)) + "."]
    labels = {row["id"]: set(row["bad_claims"]) for row in load("live_labels.jsonl")}
    labelled = []
    for r in banner + [r for r in lean if r["repeat"] == 0]:
        key = f"{r.get('cluster_id') or r.get('case_id')}:0"
        if key in labels:
            # тезисы банка Haiku не видел (в разметку уходили только тексты новостей): их исключаем
            labelled += [(c, i in labels[key]) for i, c in enumerate(r["claims"]) if c["type"] == "fact"]
    if labelled:
        bad = [c for c, b in labelled if b]
        good = [c for c, b in labelled if not b]
        lines += ["", f"Разметка Haiku, только fact (тезисы банка не размечались): утверждений {len(labelled)}, неверных {len(bad)}. "
                  "Выборку проверил вручную: 9 случайных fact-утверждений верны, оба помеченных (c13, q22) действительно обобщают мнение одного владельца бизнеса до «владельцы ждут паузы». "
                  "Числа и лексика у размеченных ответов почти везде совпадают с источником, поэтому разметка слабо разделяет правила: смотрите ручной разбор ниже.", "",
                  "| Правило | верные ошибочно отсечены | неверные пойманы |", "|---|---|---|"]
        for strict, t in ((False, 0.0), (True, 0.0), (True, 0.5), (True, 0.6), (True, 0.7), (False, 0.6)):
            hit = lambda c: (strict and bool(c["numbers_missing"])) or (t > 0 and c["lex"] < t)
            lines.append(f"| strict_numeric={strict}, lex<{t} | {sum(hit(c) for c in good)}/{len(good)} | {sum(hit(c) for c in bad)}/{len(bad)} |")
    lines.append("")


def section_ttl(lines: list[str]) -> dict:
    by_topic: dict[str, list[int]] = defaultdict(list)
    for n in load_news():
        by_topic[n.topic].append(n.shelf_life_h)
    lines += ["## 3. TTL и период полураспада по сроку актуальности (метки Haiku)", "",
              "| Тема | n | медиана, ч | p75, ч | предлагаемый TTL баннера, ч | HALF_LIFE, ч | сейчас TTL / HALF_LIFE |", "|---|---|---|---|---|---|---|"]
    current_ttl, current_half = {"rate": 72, "fx": 24}, {"rate": 72.0, "fx": 24.0}
    proposal = {}
    for topic, values in sorted(by_topic.items()):
        med, p75 = statistics.median(values), percentile(values, 75)
        ttl, half = min(TTL_CAP_H, max(6, 12 * round(p75 / 12))), max(6, round(med))
        if topic == "other":
            ttl = 36   # смесь налогов и шума: p75 не описывает типичную новость
        proposal[topic] = (ttl, half)
        lines.append(f"| {topic} | {len(values)} | {med:.0f} | {p75:.0f} | {ttl} | {half} | "
                     f"{current_ttl.get(topic, 36)} / {current_half.get(topic, 48.0):.0f} |")
    cache_h = max(t for t, _ in proposal.values())
    lines += ["", f"TTL = p75 срока актуальности, округлён до 12 ч и ограничен {TTL_CAP_H} ч (ставка: по данным 294 ч). "
              "Единственное «отсечённое» верное утверждение при lex<0.5 это ответ «в новостях нет данных» на вопрос не по теме (q17): отсечение здесь желательно. "
              "HALF_LIFE менять не предлагаю: он двигает порядок показа, а срок актуальности размечен моделью. "
              "Тема «other» смесь налоговых и шумовых новостей, для неё остаётся 36 ч. "
              f"Кэш саммари сегмента: до {cache_h} ч (сейчас 72 ч). "
              "Срок актуальности оценён моделью, не пользователями: значения как стартовые, уточнять по «не про меня» и «неточно».", ""]
    return {"ttl": proposal, "cache_h": cache_h}


def section_relevance(lines: list[str]) -> None:
    news = {n.item.news_id: n.item for n in load_news()}
    companies = {c.company.company_id: c.company for c in load_companies()}
    groups: dict[int, list[float]] = defaultdict(list)
    for p in read_jsonl("banner_pairs.jsonl"):
        groups[p["relevance"]].append(tag_overlap(companies[p["company_id"]].tags, news[p["news_id"]].tags))
    lines += ["## 4. Релевантность: пересечение тегов компании и новости по меткам пар", "", "| Метка | пар | медиана overlap | p25 | p75 |", "|---|---|---|---|---|"]
    for label in sorted(groups):
        v = groups[label]
        lines.append(f"| {label} | {len(v)} | {statistics.median(v):.2f} | {percentile(v, 25):.2f} | {percentile(v, 75):.2f} |")
    lines += ["", "Порог RELEVANCE_FLOOR=0.25 действует на 0.6×rerank + 0.4×overlap; при rerank≥0.5 новость с нулевым пересечением тегов "
              "всё равно проходит порог, значит пересечение не отсекает нерелевантное. Реальный rerank_score в синтетике не воспроизводится, поэтому порог не меняю.", ""]


RECOMMENDATIONS = """## 5. Что изменено в ветке по итогам

**Валидатор (scoring.py, generator.py, models.py)**
1. Любое число проверяемого утверждения должно быть в источнике (`STRICT_NUMERIC`). Раньше правило срабатывало только при обоснованности ниже 0.4, а одно подменённое число давало ровно 0.4 и проходило. Числа словами («полпроцента», «полтора») нормализуются: иначе честные пересказы отсекались (5 из 376 живых утверждений).
2. Порог пересечения слов с источником у каждого fact/bank-утверждения: `MIN_CLAIM_LEX = 0.5`. В живых ответах он ловит выводы, поданные как факт (q05, q07), и ответ «в новостях нет данных» (q17); самый низкий честный пересказ имеет 0.556.
3. `HIGH=0.75`, `MEDIUM=0.55`, `min_grounding=0.6` оставлены: 0.80 на сетке даёт ложные отказы без заметного выигрыша, повторы на живых ответах почти не нужны.
4. Подмена смысла при тех же числах и словах («повысил» вместо «снизил») правилами не ловится. NLI не применяем (вычисления ограничены); контроль через промпт: пересказ теми же лексемами, выводы только обоснованные.

**Чат**: маршрут deepseek-v4.1-flash первой, qwen запасной; укороченный вывод (headline до 4 слов, пустые шаблоны «для вас», follow_ups пустой). После финальной проверки p50 около 2.6 с, редкие всплески шлюза до 5-7 с.

**TTL**: значения из таблицы раздела 3 применены в `config.py`.
"""


def main() -> None:
    lines = ["# Отчёт: пороги валидатора и TTL на синтетике", "",
             "Метки синтетические (Haiku, часть проверена вручную), скилл humanizer не использовался. Результаты предварительные: "
             "честные черновики в оффлайн-прогоне копируют источник, поэтому реальные ложные отказы смотрит живой раздел.", ""]
    section_offline(lines)
    section_live(lines)
    section_ttl(lines)
    section_relevance(lines)
    lines.append(RECOMMENDATIONS)
    out = RESULTS / "REPORT.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"отчёт: {out}")


if __name__ == "__main__":
    main()
