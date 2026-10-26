"""Персонализация на данных организаторов (читаются из каталога вне проекта; в отчёт идут только агрегаты).

    uv run python scripts/eval/run_personalization.py --data-dir <каталог с df_*_hack.csv> [--sample 2000] [--seed 7]
"""
from __future__ import annotations

import argparse
import collections
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import honest_draft  # noqa: E402
from dataset import load_bank, load_bank_topics, load_news  # noqa: E402
from summary_branch.domain import scoring  # noqa: E402
from summary_branch.application.binder import DefaultEvidenceBinder  # noqa: E402
from summary_branch.application.confidence import DefaultContentScorer  # noqa: E402
from summary_branch.domain.models import (  # noqa: E402
    CompanyContext, CompanyProfile, Decision, FlowRole, PaymentProfile, Product, SummaryRequest, Mode,
)
from summary_branch.domain.personalization import enrich, segment_signature, topic_exposure, TOPICS  # noqa: E402

REPORT = Path(__file__).resolve().parents[2] / "eval" / "results" / "PERSONALIZATION.md"
PRODUCT_COLUMNS = {"CL_PRODUCT_RKO_MARK": Product.RKO, "CL_PRODUCT_GAR_MARK": Product.GUARANTEE,
                   "CL_PRODUCT_LOA_MARK": Product.LOAN, "CL_PRODUCT_LEA_MARK": Product.LEASING,
                   "CL_PRODUCT_DEP_MARK": Product.DEPOSIT, "CL_PRODUCT_ZPP_MARK": Product.PAYROLL,
                   "FACTORING_MARK": Product.FACTORING}
RELATIONSHIPS = {"CLIENT": "client", "PROSPECT": "prospect", "CLOSE_CLIENT": "close_client"}


def read_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle, delimiter=";")


def to_profile(row: dict) -> CompanyProfile:
    countries = [c.strip() for c in row["FTS_COUNTRY_NAME"].split(",") if c.strip()]
    return CompanyProfile(
        okved_code=row["MAINOKVED_CODE"], okved_name=row["MAINOKVED_NAME"], age_band=row["COMPANY_AGE_YEARS_GRADATION"],
        lifecycle=row["ECO_MARK_LABEL"], workers_band=row["AMOUNT_WORKERS_FNS_GRADATION"],
        revenue=float(row["REVENUE_COMPANY"]), region=row["REGION"], city=row["CITY"],
        relationship=RELATIONSHIPS[row["CL_TYPE"]],
        products={p for column, p in PRODUCT_COLUMNS.items() if row[column] == "1.0"},
        foreign_trade=row["FTS_FLAG"] == "1.0", trade_countries=countries,
        negative_flag=row["NEGATIVEMARK"] == "1.0", group_id=row["GROUP_ID"])


def load_profiles(data_dir: Path) -> dict[str, dict]:
    """Строки по компании; компании с повторяющимся идентификатором исключаются (в данных они противоречивы)."""
    rows, repeated = {}, set()
    for row in read_csv(data_dir / "df_Uid_data_hack.csv"):
        key = row["SYNTHETIC_U"]
        if key in rows:
            repeated.add(key)
        rows[key] = row
    for key in repeated:
        rows.pop(key)
    print(f"профилей {len(rows)}, исключено повторов {len(repeated)}", file=sys.stderr)
    return rows


def payment_profiles(data_dir: Path, rows: dict[str, dict], ids: set[str]) -> dict[str, PaymentProfile]:
    partners: dict[str, collections.Counter] = {i: collections.Counter() for i in ids}
    paid, received = collections.Counter(), collections.Counter()
    for row in read_csv(data_dir / "df_Uid_payment_hack.csv"):
        payer, beneficiary, amount = row["PAYER"], row["BENEFICIARY"], float(row["PAY_AMT"])
        if payer == beneficiary:
            continue
        if payer in ids:
            partners[payer][beneficiary] += 1
            paid[payer] += amount
        if beneficiary in ids:
            partners[beneficiary][payer] += 1
            received[beneficiary] += amount
    out = {}
    for company, counter in partners.items():
        known = [p for p in counter if p in rows]
        if not known:
            continue
        sectors = collections.Counter(rows[p]["MAINOKVED_CODE"].partition(".")[0] for p in known)
        shares = {k: round(v / len(known), 3) for k, v in sectors.items()}
        total = paid[company] + received[company]
        flow = (FlowRole.PAYER if paid[company] > 0.65 * total else
                FlowRole.RECEIVER if received[company] > 0.65 * total else FlowRole.BALANCED)
        band = "1-5" if len(counter) <= 5 else "6-25" if len(counter) <= 25 else "26+"
        out[company] = PaymentProfile(counterparty_sectors=shares, counterparty_count_band=band, flow_role=flow)
    return out


def news_signals():
    """Сигналы содержимого один раз на новость: честные черновики (содержание от компании не зависит)."""
    bank, bank_topics = load_bank(), load_bank_topics()
    placeholder = CompanyContext(company_id="x", segment_id="x")
    result = []
    for news in load_news():
        req = SummaryRequest(trace_id="eval", mode=Mode.BANNER, company=placeholder, news=[news.item],
                             bank_materials=[b for b in bank if bank_topics.get(b.doc_id) == news.topic][:1])
        from summary_branch.domain.models import SummaryDraft
        draft = SummaryDraft.model_validate(honest_draft(req))
        claims, sources = DefaultEvidenceBinder().bind(draft, req)
        signals = DefaultContentScorer().score(req, draft, claims, sources)
        result.append((news, signals, scoring.lead_from_news(news.item), draft.severity))
    return result


def decisions(profiles: list[CompanyProfile], signals_per_news) -> dict[str, collections.Counter]:
    """Распределение решений по теме новости."""
    out: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for profile in profiles:
        company = enrich(CompanyContext(company_id="p", segment_id=segment_signature(profile), profile=profile))
        for news, signals, lead, severity in signals_per_news:
            conf = scoring.company_confidence(signals, lead, news.topic, company, severity=severity)
            out[news.topic][conf.decision] += 1
    return out


def share_table(table: dict[str, collections.Counter]) -> list[str]:
    lines = ["| тема новости | показать | с пометкой | только новость | скрыть | n |", "|---|---|---|---|---|---|"]
    for topic in sorted(table):
        c = table[topic]
        n = sum(c.values())
        cells = [f"{c[d] / n:.0%}" for d in (Decision.SHOW_SUMMARY, Decision.SHOW_WITH_CAVEAT, Decision.SHOW_RAW_NEWS, Decision.DROP)]
        lines.append(f"| {topic} | " + " | ".join(cells) + f" | {n} |")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--sample", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rows = load_profiles(args.data_dir)
    all_profiles = [to_profile(r) for r in rows.values()]
    n = len(all_profiles)
    out = ["# Персонализация на данных организаторов", "",
           "Только агрегаты; идентификаторы и построчные данные в отчёт не попадают. Правила и пороги экспертные, "
           "меток реакции пользователей нет.", "", f"Профилей после исключения повторов: {n}.", ""]

    sens = collections.Counter()
    signatures = collections.Counter()
    for p in all_profiles:
        exposures = topic_exposure(p)
        signatures[segment_signature(p)] += 1
        for topic in TOPICS:
            sens[topic] += exposures[topic].value >= 0.5
    none_sensitive = sum(1 for p in all_profiles if all(e.value < 0.5 for e in topic_exposure(p).values()))
    out += ["## 1. Чувствительность и сегменты", "", "| тема | чувствительны |", "|---|---|"]
    out += [f"| {t} | {sens[t] / n:.0%} |" for t in TOPICS]
    out += ["", f"Ни к одной теме не чувствительны: {none_sensitive / n:.0%}.",
            f"Сегментов по `segment_signature`: {len(signatures)}; крупнейший {max(signatures.values()) / n:.0%}, "
            f"10 крупнейших покрывают {sum(c for _, c in signatures.most_common(10)) / n:.0%}.", ""]

    rng = random.Random(args.seed)
    ids = rng.sample(sorted(rows), min(args.sample, n))
    payments = payment_profiles(args.data_dir, rows, set(ids))
    sample = []
    for key in ids:
        profile = to_profile(rows[key])
        profile.payments = payments.get(key)
        sample.append(profile)
    with_payments = sum(p.payments is not None for p in sample)
    signals_per_news = news_signals()
    out += [f"## 2. Решения на выборке {len(sample)} профилей × {len(signals_per_news)} синтетических новостей", "",
            f"Платёжный профиль есть у {with_payments / len(sample):.0%} выборки.", ""]
    base = decisions(sample, signals_per_news)
    out += share_table(base) + [""]

    off = sum(c[Decision.SHOW_RAW_NEWS] + c[Decision.DROP] for c in base.values())
    total = sum(sum(c.values()) for c in base.values())
    out += [f"Доля пар «не показываем баннер» (только новость или скрыть): {off / total:.0%}. "
            "Пары одной компании зависимы, поэтому интервалы не считаем.", ""]

    out += ["## 3. Чувствительность к параметрам", "", "| вариант | показать+с пометкой | только новость+скрыть |", "|---|---|---|"]
    saved = (scoring.EXPOSURE_FLOOR, dict(scoring.PROFILE_WEIGHTS))
    variants = [("как сейчас", saved[0], saved[1]), ("порог 0.25", 0.25, saved[1]), ("порог 0.7", 0.7, saved[1]),
                ("вес чувствительности −20%", saved[0], {**saved[1], "exposure": 0.36, "rerank": 0.44}),
                ("вес чувствительности +20%", saved[0], {**saved[1], "exposure": 0.54, "rerank": 0.26})]
    try:
        for label, floor, weights in variants:
            scoring.EXPOSURE_FLOOR = floor
            scoring.PROFILE_WEIGHTS.update(weights)
            table = decisions(sample, signals_per_news)
            shown = sum(c[Decision.SHOW_SUMMARY] + c[Decision.SHOW_WITH_CAVEAT] for c in table.values())
            out.append(f"| {label} | {shown / total:.0%} | {1 - shown / total:.0%} |")
    finally:
        scoring.EXPOSURE_FLOOR = saved[0]
        scoring.PROFILE_WEIGHTS.update(saved[1])

    REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
