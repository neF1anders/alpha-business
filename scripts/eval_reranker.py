"""Оценка и калибровка TagReranker на data/eval/judgments.json (LLM не вызывается, всё локально).

Режимы, как в боевом конвейере (use_cases.py):
  banner: кандидаты как у retriever (есть пересечение тегов), require_tag_match=True, k=3
  chat:   все новости, require_tag_match=False, k=5
Калибруются только веса, на которые отвечает разметка: доли relevance и retrieval, веса тегов factor и sector,
масштаб модификаторов продуктов. Свежесть и источник судья не видел, поэтому их веса не трогаем.
Калибровка проверяется 4-кратной кросс-валидацией по компаниям; итоговые значения подбираются на всех 24.

Запуск: python scripts/eval_reranker.py   (пишет data/eval/report.json, константы ranking.py не меняет)
"""
from __future__ import annotations

import itertools
import json
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator.application.use_cases import MATCH_NAMESPACES  # noqa: E402
from orchestrator.domain.metrics import ndcg_at_k, noise_rate, precision_at_k  # noqa: E402
from orchestrator.domain.models import CompanyProfile, NewsItem, SourceKind  # noqa: E402
from orchestrator.domain.ranking import NAMESPACE_WEIGHT, TagReranker, W_FRESHNESS, W_RELEVANCE, W_RETRIEVAL, W_SOURCE  # noqa: E402

DATA = ROOT / "data" / "eval" / "judgments.json"
REPORT = ROOT / "data" / "eval" / "report.json"
NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
K_BANNER, K_CHAT = 3, 5
FOLDS = 4
RELEVANCE_BUDGET = W_RELEVANCE + W_RETRIEVAL          # делим между relevance и retrieval, свежесть и источник фиксированы


def load():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    news = {n["news_id"]: NewsItem(
        news_id=n["news_id"], cluster_id=n["cluster_id"], title=n["title"], text=n["text"], url="",
        published_at=NOW - timedelta(days=n["age_days"]), source_kind=SourceKind(n["source_kind"]),
        tags=frozenset(n["tags"])) for n in d["news"]}
    grades = {(j["company"], j["news_id"]): j["grade"] for j in d["judgments"]}
    companies = []
    for c in d["companies"]:
        profile = CompanyProfile(c["id"], c["segment_id"], frozenset(c["tags"]))
        companies.append((profile, {nid: grades[(c["id"], nid)] for nid in news}))
    return companies, news


def candidates(profile, news, banner):
    """Эмулирует InMemoryNewsRetriever: retrieval_score = доля тегов новости, совпавших с тегами компании."""
    q = profile.tags_in(*MATCH_NAMESPACES)
    out = []
    for it in news.values():
        hit = len(it.tags & q) / len(it.tags) if it.tags else 0.0
        if banner and hit == 0:
            continue
        out.append(NewsItem(**{**it.__dict__, "retrieval_score": hit}))
    return out


def dedupe(ids_in_order, news):
    seen, out = set(), []
    for nid in ids_in_order:
        if news[nid].cluster_id not in seen:
            seen.add(news[nid].cluster_id)
            out.append(nid)
    return out


def evaluate(order_fn, companies, news):
    """order_fn(profile, items, k, banner) -> список news_id. Возвращает метрики, усреднённые по компаниям."""
    p3, n3, n5, noise, covered = [], [], [], [], 0
    for profile, g in companies:
        all_grades = list(g.values())
        shown_b = [g[i] for i in order_fn(profile, candidates(profile, news, True), K_BANNER, True)]
        shown_c = [g[i] for i in order_fn(profile, candidates(profile, news, False), K_CHAT, False)]
        p3.append(precision_at_k(shown_b, K_BANNER))
        n3.append(ndcg_at_k(shown_b, all_grades, K_BANNER))
        n5.append(ndcg_at_k(shown_c, all_grades, K_CHAT))
        if shown_b:
            covered += 1
            noise.append(noise_rate(shown_b))
    m = {"P@3(грейд 2)": statistics.mean(p3), "NDCG@3 баннеры": statistics.mean(n3),
         "NDCG@5 чат": statistics.mean(n5), "шум в баннерах": statistics.mean(noise) if noise else 0.0,
         "покрытие": covered / len(companies)}
    m["J"] = (m["NDCG@3 баннеры"] + m["NDCG@5 чат"]) / 2
    return m


def reranker_order(reranker):
    def order(profile, items, k, banner):
        ranked = reranker.rerank(profile, items, k, require_tag_match=banner, now=NOW)
        return [r.item.news_id for r in ranked]
    return order


def retrieval_only(news):
    def order(profile, items, k, banner):
        ids = [i.news_id for i in sorted(items, key=lambda i: (-i.retrieval_score, i.news_id))]
        return dedupe(ids, news)[:k]
    return order


def random_order(news, seed=0):
    rng = random.Random(seed)

    def order(profile, items, k, banner):
        ids = [i.news_id for i in items]
        rng.shuffle(ids)
        return dedupe(ids, news)[:k]
    return order


def average_random(companies, news, runs=200):
    fn = random_order(news)
    runs_m = [evaluate(fn, companies, news) for _ in range(runs)]
    return {k: statistics.mean(m[k] for m in runs_m) for k in runs_m[0]}


def oracle(companies, news):
    def order_for(profile_grades):
        def order(profile, items, k, banner):
            g = profile_grades[profile.company_id]
            ids = [i.news_id for i in sorted(items, key=lambda i: (-g[i.news_id], i.news_id))]
            return dedupe(ids, news)[:k]
        return order
    return order_for({p.company_id: g for p, g in companies})


# ---- калибровка ----
def grid():
    for share, wf, ws, scale in itertools.product(
            (0.30, 0.40, 0.50, 0.55, 0.60, 0.70, 0.75), (0.3, 0.5, 0.7, 0.9), (0.2, 0.35, 0.5, 0.7, 0.9), (0.0, 0.5, 1.0, 2.0)):
        yield {"relevance_share": share, "factor": wf, "sector": ws, "modifier_scale": scale}


def make_reranker(p):
    w_rel = round(p["relevance_share"], 4)
    w_ret = round(RELEVANCE_BUDGET - w_rel, 4)
    return TagReranker(weights=(w_rel, w_ret, W_FRESHNESS, W_SOURCE),
                       namespace_weight={"factor": p["factor"], "sector": p["sector"]}, modifier_scale=p["modifier_scale"])


DEFAULT_P = {"relevance_share": W_RELEVANCE, "factor": NAMESPACE_WEIGHT["factor"],
             "sector": NAMESPACE_WEIGHT["sector"], "modifier_scale": 1.0}


def fit(companies, news):
    best, best_j = None, -1.0
    for p in grid():
        j = evaluate(reranker_order(make_reranker(p)), companies, news)["J"]
        if j > best_j + 1e-9:                       # при равенстве остаётся ближе к началу сетки: порядок сетки не выбирает
            best, best_j = p, j
    return best, best_j


def fmt(m):
    return " ".join(f"{k}={v:.3f}" for k, v in m.items())


def main() -> None:
    companies, news = load()
    rows = {
        "случайный порядок": average_random(companies, news),
        "только retrieval": evaluate(retrieval_only(news), companies, news),
        "текущие веса": evaluate(reranker_order(TagReranker()), companies, news),
    }
    final, fit_j = fit(companies, news)
    rows["калиброванные (на всех, оптимистично)"] = evaluate(reranker_order(make_reranker(final)), companies, news)
    rows["потолок (оракул)"] = evaluate(oracle(companies, news), companies, news)

    # честная оценка: подбор на 3/4 компаний, проверка на оставшейся 1/4
    rng = random.Random(7)
    idx = list(range(len(companies)))
    rng.shuffle(idx)
    folds = [idx[i::FOLDS] for i in range(FOLDS)]
    cv_default, cv_fit = [], []
    for held in folds:
        train = [companies[i] for i in idx if i not in held]
        test = [companies[i] for i in held]
        p, _ = fit(train, news)
        cv_default.append(evaluate(reranker_order(TagReranker()), test, news)["J"])
        cv_fit.append(evaluate(reranker_order(make_reranker(p)), test, news)["J"])

    print(f"компаний: {len(companies)}, новостей: {len(news)}\n")
    for name, m in rows.items():
        print(f"{name:42s} {fmt(m)}")
    print(f"\nкросс-валидация ({FOLDS} фолда), J на отложенных компаниях:")
    print(f"  текущие веса: {statistics.mean(cv_default):.3f}   калиброванные: {statistics.mean(cv_fit):.3f}   "
          f"(по фолдам: {[round(b - a, 3) for a, b in zip(cv_default, cv_fit)]})")
    print(f"\nитоговые параметры: {final}  (по умолчанию: {DEFAULT_P})")
    REPORT.write_text(json.dumps({
        "metrics": rows, "cv_J_default": cv_default, "cv_J_fitted": cv_fit, "fitted_params": final,
        "default_params": DEFAULT_P,
        "weights_if_applied": {"W_RELEVANCE": round(final["relevance_share"], 4),
                               "W_RETRIEVAL": round(RELEVANCE_BUDGET - final["relevance_share"], 4),
                               "NAMESPACE_WEIGHT": {"factor": final["factor"], "sector": final["sector"]},
                               "modifier_scale": final["modifier_scale"]}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"отчёт: {REPORT}")


if __name__ == "__main__":
    main()
