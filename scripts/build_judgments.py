"""Эталонная выборка для калибровки реранкера: компании x новости с оценками LLM-судьи.

Один запуск: 1 запрос генерирует недостающие новости, затем 1 запрос на компанию ставит оценки 0/1/2.
Повторов нет. Если data/eval/judgments.json уже есть, скрипт останавливается, чтобы не тратить бюджет дважды.
Ключ берётся из LLM_API_KEY (окружение или .env), в результат не пишется.

Запуск: python scripts/build_judgments.py
"""
from __future__ import annotations

import csv
import json
import os
import random
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator.domain.models import utcnow  # noqa: E402
from orchestrator.domain.profile_rules import segment_of  # noqa: E402
from orchestrator.infrastructure.sample_news import sample_news  # noqa: E402

TAGS_PATH = ROOT / "data" / "processed" / "company_tags.csv"
OUT_PATH = ROOT / "data" / "eval" / "judgments.json"
NEWS_PATH = ROOT / "data" / "eval" / "news_pool.json"
URLS = ["https://changellenge.up.railway.app/v1", "https://api.alfahack-gateway26.ru/v1"]
MODEL = "deepseek/deepseek-v4.1-flash"
N_COMPANIES = 24
N_NEWS = 15
SEED = 20261025
TIMEOUT_S = 180

FACTORS = ["factor:key_rate", "factor:fx", "factor:commodities", "factor:consumer_demand", "factor:construction_market"]
SECTORS = ["agro", "mining", "manufacturing", "energy", "utilities", "construction", "trade", "transport", "horeca",
           "it_telecom", "finance", "real_estate", "prof_services", "admin_services", "education", "health",
           "culture_sport", "other_services"]
# Темы по PDF-отчётам банка; LLM пишет по ним выдуманные заметки, чтобы пул покрывал разные отрасли.
TOPICS = [
    "инфляция в августе и недельные данные Росстата", "цены на золото и спрос на защитные активы",
    "рынок облигаций и доходности ОФЗ", "металлургия: цены на сталь и экспорт",
    "потребительский спрос и розничные продажи", "рынок ИТ и телекома: спрос на импортозамещение",
    "агросектор: урожай и экспортные пошлины", "туризм и общепит: сезонный спрос",
    "энергетика: тарифы и цены на газ", "рынок труда и рост зарплат",
]


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def chat(key: str, prompt: str) -> tuple[dict, dict]:
    """Один вызов с резервным адресом шлюза при сетевой ошибке. Без повторов по статусам."""
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0,
                       "max_tokens": 4000, "reasoning": {"enabled": False},
                       "response_format": {"type": "json_object"}}, ensure_ascii=False).encode("utf-8")
    last: Exception | None = None
    for base in URLS:
        req = urllib.request.Request(base + "/chat/completions", data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = payload["choices"][0]["message"]["content"].strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(text), payload.get("usage") or {}
        except urllib.error.HTTPError as exc:   # шлюз ответил: на другой адрес не переключаемся
            raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
    raise RuntimeError(f"шлюз недоступен: {last}")


def pick_companies() -> list[dict]:
    tags: dict[str, set[str]] = defaultdict(set)
    with TAGS_PATH.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            tags[row["synthetic_u"]].add(row["tag"])
    by_segment: dict[str, list[str]] = defaultdict(list)
    for cid, ts in tags.items():
        seg = segment_of(ts)
        if "unknown" not in seg:
            by_segment[seg].append(cid)
    rng = random.Random(SEED)
    top = [s for s, _ in Counter({s: len(v) for s, v in by_segment.items()}).most_common(N_COMPANIES)]
    out = []
    for i, seg in enumerate(top, 1):
        cid = rng.choice(sorted(by_segment[seg]))
        keep = sorted(t for t in tags[cid] if t.split(":", 1)[0] in
                      ("sector", "size", "factor", "product", "okved", "region", "country", "workers", "stage"))
        out.append({"id": f"C{i}", "source_id": cid, "segment_id": seg, "tags": keep})
    return out


def base_news() -> list[dict]:
    out = []
    now = utcnow()
    for it in sample_news(now):
        age = round((now - it.published_at).total_seconds() / 86400, 1)
        out.append({"news_id": it.news_id, "cluster_id": it.cluster_id, "title": it.title, "text": it.text,
                    "tags": sorted(it.tags), "source_kind": it.source_kind.value, "age_days": age,
                    "origin": "sample_news"})
    return out


def generate_news(key: str, existing: list[dict], n: int) -> tuple[list[dict], dict]:
    prompt = (
        f"Придумай {n} коротких новостей (выдуманных, для тестовой выборки) для российского бизнес-ассистента банка. "
        "Темы, по одной новости на тему (возьми любые подходящие): " + "; ".join(TOPICS) + ".\n"
        "Для каждой: title (до 100 символов), text (1-2 предложения), tags (список из допустимых), "
        "source_kind (primary|bank|media), age_days (число от 0 до 30).\n"
        f"Допустимые теги: {', '.join(FACTORS)}, а также sector:<значение>, где значение из: {', '.join(SECTORS)}. "
        "Ставь только теги, действительно относящиеся к новости, от 1 до 3 штук. "
        "Не повторяй темы этих уже имеющихся новостей: " + "; ".join(e["title"] for e in existing) + ".\n"
        'Ответ строго JSON: {"news": [{"title": "...", "text": "...", "tags": ["..."], '
        '"source_kind": "media", "age_days": 3}]}')
    data, usage = chat(key, prompt)
    allowed = set(FACTORS) | {f"sector:{s}" for s in SECTORS}
    out = []
    for i, raw in enumerate(data["news"][:n], 1):
        tags = sorted(t for t in raw.get("tags", []) if t in allowed)
        kind = raw.get("source_kind") if raw.get("source_kind") in ("primary", "bank", "media") else "media"
        nid = f"g{i}"
        out.append({"news_id": nid, "cluster_id": nid, "title": str(raw["title"]), "text": str(raw["text"]),
                    "tags": tags, "source_kind": kind, "age_days": float(raw.get("age_days", 3)),
                    "origin": "llm"})
    return out, usage


def describe(company: dict) -> str:
    return "\n".join(f"- {t}" for t in company["tags"])


def judge(key: str, company: dict, news: list[dict]) -> tuple[list[dict], dict]:
    items = "\n".join(f"[{n['news_id']}] {n['title']}. {n['text']}" for n in news)
    prompt = (
        "Ты эксперт банка по малому и среднему бизнесу. Оцени, насколько каждая новость важна для компании.\n"
        "Профиль компании (теги: sector - отрасль, size - размер, factor - чувствительность к макрофактору, "
        "product - продукты банка, okved - вид деятельности, region - регион, country - страны внешней торговли):\n"
        f"{describe(company)}\n\nНовости:\n{items}\n\n"
        "Шкала: 0 - не важна для этой компании; 1 - полезно знать; 2 - критична, прямо влияет на её деньги, "
        "расходы или решения. Оценивай только по смыслу новости и профилю, не по длине текста. "
        'Ответ строго JSON: {"grades": [{"news_id": "...", "grade": 0, "reason": "до 15 слов"}]} '
        f"по одной записи на каждую из {len(news)} новостей.")
    data, usage = chat(key, prompt)
    valid = {n["news_id"] for n in news}
    out = [{"news_id": g["news_id"], "grade": int(g["grade"]), "reason": str(g.get("reason", ""))}
           for g in data["grades"] if g.get("news_id") in valid and int(g["grade"]) in (0, 1, 2)]
    return out, usage


def main() -> None:
    if OUT_PATH.exists():
        sys.exit(f"{OUT_PATH} уже есть: повторный запуск отменён, чтобы не тратить лимиты")
    key = os.environ.get("LLM_API_KEY") or read_env(ROOT / ".env").get("LLM_API_KEY")
    if not key:
        sys.exit("не найден LLM_API_KEY ни в окружении, ни в .env")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    companies = pick_companies()
    news = base_news()
    generated, usage = generate_news(key, news, N_NEWS - len(news))
    news += generated
    NEWS_PATH.write_text(json.dumps(news, ensure_ascii=False, indent=2), encoding="utf-8")   # сохранить сразу
    print(f"новостей: {len(news)} ({len(generated)} от LLM), токены {usage}")

    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    tokens = Counter(usage)

    def run(company: dict) -> None:
        try:
            grades, u = judge(key, company, news)
            results[company["id"]] = grades
            tokens.update({k: v for k, v in u.items() if isinstance(v, int)})
        except Exception as exc:  # noqa: BLE001 - ошибку записываем, повтора нет
            errors[company["id"]] = f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, companies))

    judgments = [{"company": cid, **g} for cid in sorted(results, key=lambda c: int(c[1:])) for g in results[cid]]
    OUT_PATH.write_text(json.dumps({
        "meta": {"model": MODEL, "seed": SEED, "scale": "0 не важна, 1 полезно, 2 критична", "tokens": dict(tokens),
                 "errors": errors},
        "companies": companies, "news": news, "judgments": judgments}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"компаний оценено: {len(results)}/{len(companies)}, оценок: {len(judgments)}, ошибок: {len(errors)}")
    for cid, msg in errors.items():
        print(f"  {cid}: {msg}")
    print(f"сохранено: {OUT_PATH}")


if __name__ == "__main__":
    main()
