"""Оффлайн-прогон валидатора без LLM: честные и испорченные черновики -> сигналы -> решения при разных порогах.

uv run python scripts/eval/run_offline_sweep.py   (результат: eval/results/offline.json)
"""
from __future__ import annotations

import itertools
import json
from collections import defaultdict
from pathlib import Path

from cases import EXTENDED_PERTURBATIONS, DraftCase, build_cases
from stats import rate
from summary_branch.domain import scoring
from summary_branch.application.binder import DefaultEvidenceBinder
from summary_branch.domain.models import Decision, SummaryDraft
from summary_branch.domain.scoring import company_confidence, content_signals, lead_from_news, passes_validation

RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results" / "offline.json"
RERANK = 0.9   # допущение: реранкер уверен в новости; релевантность здесь не исследуется

GRID = {
    "HIGH": [0.65, 0.70, 0.75, 0.80, 0.85, 0.90],
    "MEDIUM": [0.45, 0.50, 0.55, 0.60, 0.65],
    "NUMERIC_MISS_GROUNDING": [0.3, 0.4, 0.5, 0.6, 0.7],
    "min_grounding": [0.5, 0.6, 0.7, 0.8],
}
# Правила STRICT_NUMERIC и MIN_CLAIM_LEX теперь в scoring.py; свип подменяет их на время вызова. BASELINE ниже
# (оба выключены) воспроизводит поведение до их включения, поэтому отчёт «было/стало» остаётся сопоставимым.
RULES = {"strict_numeric": [False, True], "min_claim_lex": [0.0, 0.4, 0.5, 0.6, 0.7]}
RULE_GRID = {"HIGH": [0.70, 0.75, 0.80, 0.85], "MEDIUM": [0.55], "NUMERIC_MISS_GROUNDING": [0.4],
             "min_grounding": [0.6], **RULES}
BASELINE = {"HIGH": 0.75, "MEDIUM": 0.55, "NUMERIC_MISS_GROUNDING": 0.4, "min_grounding": 0.6,
            "strict_numeric": False, "min_claim_lex": 0.0}
# Правила смысла (направление, единицы, названия, вывод без опоры) добавлены позже: сравниваются отдельно от числовых.
MEANING_OFF = {"direction": False, "unit": False, "entity": False, "inference": False}
MEANING_ON = {key: True for key in MEANING_OFF}
NUMERIC_RULES = {**BASELINE, "strict_numeric": True, "min_claim_lex": 0.5}



def evaluate(case: DraftCase, params: dict) -> dict:
    """Один черновик при заданных порогах. Константы scoring подменяются на время вызова."""
    patched = {"HIGH": params["HIGH"], "MEDIUM": params["MEDIUM"],
               "NUMERIC_MISS_GROUNDING": params["NUMERIC_MISS_GROUNDING"],
               "STRICT_NUMERIC": params["strict_numeric"], "MIN_CLAIM_LEX": params["min_claim_lex"],
               "DIRECTION_CHECK": params.get("direction", False), "UNIT_CHECK": params.get("unit", False),
               "ENTITY_CHECK": params.get("entity", False), "INFERENCE_CHECK": params.get("inference", False)}
    old = {k: getattr(scoring, k) for k in patched}
    try:
        for key, value in patched.items():
            setattr(scoring, key, value)
        draft = SummaryDraft.model_validate(case.draft)
        claims, sources = DefaultEvidenceBinder().bind(draft, case.request)
        signals = content_signals(case.request, draft, claims, sources)
        news = case.request.news[0].model_copy(update={"rerank_score": RERANK})
        confidence = company_confidence(signals, lead_from_news(news), draft.topic, case.request.company,
                                        has_sources=bool(sources))
    finally:
        for key, value in old.items():
            setattr(scoring, key, value)
    valid = passes_validation(signals, params["min_grounding"])
    score, decision = confidence.score, confidence.decision.value
    return {"valid": valid, "decision": decision, "score": score,
            "grounding": signals.grounding, "numeric_miss": signals.numeric_miss, "structure_ok": signals.structure_ok}


def eligible(cases: list[DraftCase]) -> list[DraftCase]:
    """Только новости, где честный черновик при текущих порогах доходит до «высокой»: так мерим валидатор,
    а не свежесть и релевантность (старые новости получают низкую уверенность и без порчи)."""
    ok = {c.case_id.split(":")[0] for c in cases
          if c.kind == "honest" and evaluate(c, BASELINE)["decision"] == Decision.SHOW_SUMMARY.value}
    return [c for c in cases if c.case_id.split(":")[0] in ok]


def metrics(cases: list[DraftCase], params: dict) -> dict:
    good = [c for c in cases if c.is_valid]
    bad = [c for c in cases if not c.is_valid]
    good_r = [evaluate(c, params) for c in good]
    bad_r = [evaluate(c, params) for c in bad]
    summary, caveat = Decision.SHOW_SUMMARY.value, Decision.SHOW_WITH_CAVEAT.value
    return {
        "params": params,
        "fa_summary": sum(r["decision"] == summary for r in bad_r), "bad": len(bad),
        "fa_shown": sum(r["decision"] in (summary, caveat) for r in bad_r),
        # если после повторов невалидный результат принудительно становится «только новость»
        "fa_summary_gated": sum(r["decision"] == summary and r["valid"] for r in bad_r),
        "fr_summary": sum(r["decision"] != summary for r in good_r), "good": len(good),
        "fr_summary_gated": sum(not (r["decision"] == summary and r["valid"]) for r in good_r),
        "retry_good": sum(not r["valid"] for r in good_r),    # зря потраченные повторы
        "retry_bad": sum(not r["valid"] for r in bad_r),      # повторы, которые что-то ловят
    }


def by_kind(cases: list[DraftCase], params: dict) -> dict[str, dict]:
    table: dict[str, dict] = defaultdict(lambda: {"n": 0, "summary": 0, "shown": 0, "invalid": 0})
    for case in cases:
        r = evaluate(case, params)
        row = table[case.kind]
        row["n"] += 1
        row["summary"] += r["decision"] == Decision.SHOW_SUMMARY.value
        row["shown"] += r["decision"] in (Decision.SHOW_SUMMARY.value, Decision.SHOW_WITH_CAVEAT.value)
        row["invalid"] += not r["valid"]
    return dict(table)


def report(title: str, cases: list[DraftCase], configs: dict[str, dict]) -> None:
    print(f"\n{title}: честных {sum(c.is_valid for c in cases)}, испорченных {sum(not c.is_valid for c in cases)}")
    for name, params in configs.items():
        m = metrics(cases, params)
        print(f"  {name}")
        print("    ложно принято как «высокая»:", rate(m["fa_summary"], m["bad"]))
        print("    ложный отказ (честных не дошло до «высокой»):", rate(m["fr_summary"], m["good"]))


def main() -> None:
    all_cases = build_cases()
    cases = eligible(all_cases)
    print(f"новостей с честным «высокая»: {len({c.case_id.split(':')[0] for c in cases})} из "
          f"{len({c.case_id.split(':')[0] for c in all_cases})}")
    print(f"случаев: {len(cases)} (честных {sum(c.is_valid for c in cases)}, испорченных {sum(not c.is_valid for c in cases)})")
    base = metrics(cases, BASELINE)
    configs = {
        "до числовых правил (STRICT_NUMERIC и MIN_CLAIM_LEX выключены)": BASELINE,
        "числовые правила (STRICT_NUMERIC + MIN_CLAIM_LEX)": NUMERIC_RULES,
        "+ правила смысла (направление, единицы, названия, вывод без опоры)": {**NUMERIC_RULES, **MEANING_ON},
    }
    ablation = {name: {**NUMERIC_RULES, **MEANING_OFF, key: True} for name, key in
                (("только направление", "direction"), ("только единицы", "unit"), ("только названия", "entity"),
                 ("только вывод без опоры", "inference"))}
    report("Исходный набор", cases, {**configs, **ablation})
    extended = eligible(build_cases(extended=True))
    new_kinds = [c for c in extended if c.is_valid or c.kind in EXTENDED_PERTURBATIONS]
    report("Новые виды порчи (направление, единицы, чужая сущность)", new_kinds,
           {**configs, **ablation})
    meaning = {**NUMERIC_RULES, **MEANING_ON}
    print("\nПо видам порчи (числовые правила -> + правила смысла): n / «высокая» / «высокая» после правил смысла")
    for kind in dict.fromkeys(c.kind for c in extended):
        group = [c for c in extended if c.kind == kind]
        before = sum(evaluate(c, NUMERIC_RULES)["decision"] == Decision.SHOW_SUMMARY.value for c in group)
        after = sum(evaluate(c, meaning)["decision"] == Decision.SHOW_SUMMARY.value for c in group)
        print(f"  {kind:22s} {len(group):3d} {before:3d} {after:3d}")
    print("\nПо видам порчи (прежнее поведение): n / «высокая» / показано / невалидно (повтор)")
    kinds = by_kind(cases, BASELINE)
    for kind, row in kinds.items():
        print(f"  {kind:22s} {row['n']:3d} {row['summary']:3d} {row['shown']:3d} {row['invalid']:3d}")
    sweep = [metrics(cases, {**BASELINE, **dict(zip(GRID, values))}) for values in itertools.product(*GRID.values())]
    rules = [metrics(cases, dict(zip(RULE_GRID, values))) for values in itertools.product(*RULE_GRID.values())]
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps({"baseline": base, "by_kind": kinds, "sweep": sweep, "rules": rules,
                                   "cases": len(cases)}, ensure_ascii=False), encoding="utf-8")
    print(f"\nсетка порогов: {len(sweep)}, сетка с правилами: {len(rules)} -> {RESULTS}")


if __name__ == "__main__":
    main()
