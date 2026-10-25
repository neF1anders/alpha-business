"""Живой прогон на шлюзе под контролем бюджета: чат (задержка и качество) и баннеры.

uv run python scripts/eval/run_live.py pilot     # 3 вопроса чата + 3 кластера баннеров, оценка цены вызова
uv run python scripts/eval/run_live.py chat [повторов]
uv run python scripts/eval/run_live.py banner
Результаты: eval/results/live_chat.jsonl, live_banner.jsonl. Ключ нигде не печатается.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from budget import BudgetGuard, BudgetStop, allowed_calls
from cases import banks_for, best_company
from dataset import DATA_DIR, load_bank, load_bank_topics, load_companies, load_news, read_jsonl
from summary_branch.bootstrap import build_branch
from summary_branch.fakes import StaticDirectory
from summary_branch.llm_gateway import GatewaySettings, OpenAICompatibleLLMClient
from summary_branch.models import ContentKey, Mode, SummaryRequest
from summary_branch.scoring import _evidence_text, _numbers, _stems

RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"
CEILING_RUB = 100.0
PLANNED_BUDGET_RUB = 80.0
HARD_CAP_CALLS = 250
NEWS = {n.item.news_id: n for n in load_news(rerank_score=0.9)}
COMPANIES = {c.company.company_id: c for c in load_companies()}
BANK, BANK_TOPICS = load_bank(), load_bank_topics()


def cluster_of(news_id: str) -> list:
    cluster_id = NEWS[news_id].item.cluster_id
    return [n.item for n in NEWS.values() if n.item.cluster_id == cluster_id]


def claim_stats(claims, req: SummaryRequest) -> list[dict]:
    """По каждому проверяемому утверждению: чего нет в источнике (для правил strict_numeric и min_claim_lex)."""
    out = []
    for c in claims:
        if c.type.value not in ("fact", "bank"):
            continue
        evidence = _evidence_text(c, req)
        stems = _stems(c.text)
        out.append({"type": c.type.value, "text": c.text, "grounding": c.grounding,
                    "numbers_missing": sorted(_numbers(c.text) - _numbers(evidence)),
                    "lex": round(len(stems & _stems(evidence)) / len(stems), 3) if stems else 1.0})
    return out


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_chat(llm, guard: BudgetGuard, cases: list[dict], repeats: int, out: Path) -> int:
    done = 0
    for case in cases:
        news, company = NEWS[case["news_id"]], COMPANIES[case["company_id"]]
        for repeat in range(repeats):
            if done and done % 10 == 0:
                guard.check()
            req = SummaryRequest(trace_id=f"{case['case_id']}-{repeat}", mode=Mode.CHAT, question=case["question"],
                                 company=company.company, news=cluster_of(case["news_id"]),
                                 bank_materials=banks_for(news, BANK, BANK_TOPICS))
            rt = build_branch(llm, StaticDirectory([company.company]))
            calls0 = llm.calls
            started = time.monotonic()
            answer = rt.branch.handle_chat(req)
            elapsed = time.monotonic() - started
            append(out, {"case_id": case["case_id"], "repeat": repeat, "seconds": round(elapsed, 2),
                         "calls": llm.calls - calls0, "raw_news": answer.raw_news, "caveat": answer.caveat,
                         "confidence": answer.confidence, "on_topic": case["on_topic"],
                         "company_relevant": case["company_relevant"],
                         "claims": claim_stats(answer.claims, req),
                         "all_claims": [{"type": c.type.value, "text": c.text} for c in answer.claims],
                         "follow_ups": answer.follow_ups, "question": case["question"],
                         "source_text": [n.text for n in req.news]})
            done += 1
    return done


def run_banner(llm, guard: BudgetGuard, out: Path, limit: int | None = None) -> int:
    done = 0
    seen: set[str] = set()
    for row in NEWS.values():
        cluster_id = row.item.cluster_id
        if cluster_id in seen or (limit is not None and done >= limit):
            continue
        seen.add(cluster_id)
        if done and done % 10 == 0:
            guard.check()
        company = best_company(row, list(COMPANIES.values()))
        req = SummaryRequest(trace_id=f"b-{cluster_id}", mode=Mode.BANNER, company=company.company,
                             news=cluster_of(row.item.news_id), bank_materials=banks_for(row, BANK, BANK_TOPICS))
        rt = build_branch(llm, StaticDirectory([company.company]))
        calls0 = llm.calls
        started = time.monotonic()
        banners = rt.branch.handle_banner(req)
        elapsed = time.monotonic() - started
        key = ContentKey(cluster_id=cluster_id, segment_id=company.company.segment_id, prompt_version="v1")
        seg = rt.cache.get(key)
        append(out, {"cluster_id": cluster_id, "news_id": row.item.news_id, "topic": row.topic, "seconds": round(elapsed, 2),
                     "calls": llm.calls - calls0, "banner": bool(banners),
                     "decision": banners[0].decision.value if banners else None,
                     "confidence": banners[0].confidence if banners else None,
                     "signals": seg.signals.model_dump() if seg else None,
                     "claims": claim_stats(seg.claims, req) if seg else [],
                     "all_claims": [{"type": c.type.value, "text": c.text} for c in seg.claims] if seg else [],
                     "headline": seg.headline if seg else None,
                     "source_text": [n.text for n in req.news]})
        done += 1
    return done


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pilot"
    settings = GatewaySettings.from_env(str(Path(__file__).resolve().parents[2] / ".env"))
    guard = BudgetGuard(settings.base_urls[0], settings.api_key.get_secret_value(), ceiling=CEILING_RUB)
    print(f"старт: потрачено на ключе {guard.start.spend:.2f}, остаток {guard.start.remaining:.2f}")
    if guard.start.remaining is not None and guard.start.remaining < 200:
        raise SystemExit("остаток дня меньше 200 руб: не стартую, согласуйте с владельцем")
    llm = OpenAICompatibleLLMClient(settings)
    chat_cases = read_jsonl("chat_cases.jsonl")
    try:
        if mode == "pilot":
            n = run_chat(llm, guard, chat_cases[:3], 1, RESULTS / "pilot_chat.jsonl")
            n += run_banner(llm, guard, RESULTS / "pilot_banner.jsonl", limit=3)
            time.sleep(3)   # учёт трат на шлюзе приходит с задержкой
            spent = guard.spent
            per_call = spent / max(llm.calls, 1)
            print(f"пилот: вызовов {llm.calls}, потрачено {spent:.3f}, ≈{per_call:.4f} руб/вызов, "
                  f"допустимо вызовов в основном прогоне: {allowed_calls(per_call, PLANNED_BUDGET_RUB, HARD_CAP_CALLS)}")
        elif mode == "chat":
            repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 2
            run_chat(llm, guard, chat_cases, repeats, RESULTS / "live_chat.jsonl")
        elif mode == "banner":
            run_banner(llm, guard, RESULTS / "live_banner.jsonl")
        else:
            raise SystemExit(f"неизвестный режим {mode!r}")
    except BudgetStop as stop:
        print("СТОП БЮДЖЕТА:", stop)
    time.sleep(3)
    print(f"итог: вызовов {llm.calls}, токены {llm.prompt_tokens}+{llm.completion_tokens}, "
          f"потрачено нами {guard.spent:.3f} руб (потолок {CEILING_RUB})")


if __name__ == "__main__":
    main()
