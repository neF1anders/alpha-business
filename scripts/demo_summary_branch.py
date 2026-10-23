"""Демо ветки: два сегмента, три компании, повторный прогон идёт через кэш без вызовов LLM.

Запуск: uv run python scripts/demo_summary_branch.py          (заглушка LLM, без сети)
        uv run python scripts/demo_summary_branch.py --live   (реальный шлюз, ключ из .env, тратит бюджет)
"""
import logging
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from summary_branch.bootstrap import build_branch  # noqa: E402
from summary_branch.fakes import FakeLLMClient, StaticDirectory  # noqa: E402
from summary_branch.llm_gateway import OpenAICompatibleLLMClient  # noqa: E402
from summary_branch.models import (  # noqa: E402
    BankMaterialRef, CompanyContext, Mode, RankedNewsItem, SourceKind, SummaryRequest, utcnow,
)

COMPANIES = [
    CompanyContext(company_id="metal-1", segment_id="metal-large", tags=["rate", "credit", "metal"],
                   features=["3 кредитных продукта"]),
    CompanyContext(company_id="metal-2", segment_id="metal-large", tags=["rate", "metal", "export"],
                   features=["экспорт в ЕАЭС"]),
    CompanyContext(company_id="agro-1", segment_id="agro-small", tags=["fx", "agro"],
                   features=["валютная выручка"]),
]


def news(news_id, cluster_id, title, text, tags, score, kind=SourceKind.PRIMARY, hours_ago=3):
    return RankedNewsItem(news_id=news_id, cluster_id=cluster_id, title=title, text=text, source_kind=kind,
                          url=f"https://example.com/{news_id}", published_at=utcnow() - timedelta(hours=hours_ago),
                          tags=tags, rerank_score=score)


REQUESTS = [
    SummaryRequest(
        trace_id="demo-metal", mode=Mode.BANNER, company=COMPANIES[0],
        news=[
            news("n1", "c-rate", "Банк России сохранил ключевую ставку на уровне 14%",
                 "Совет директоров 11 сентября сохранил ключевую ставку на уровне 14,0%. Это первая пауза.",
                 ["rate", "credit"], 0.92),
            news("n2", "c-labor", "Рост зарплат в промышленности замедлился до 9%",
                 "Рост номинальных зарплат в промышленности в августе замедлился до 9%.",
                 ["labor", "metal"], 0.7, SourceKind.MEDIA, 10),
        ],
        bank_materials=[BankMaterialRef(doc_id="d1", title="Макрообзор", page=4, url="https://alfabank.ru/doc",
                                        claim_text="Аналитики банка ожидают ставку 14% до конца года.")]),
    SummaryRequest(
        trace_id="demo-agro", mode=Mode.BANNER, company=COMPANIES[2],
        news=[news("n3", "c-fx", "Курс юаня вырос до 12,5 рубля",
                   "Курс юаня на торгах вырос до 12,5 рубля. Экспортёры наращивают продажи валютной выручки.",
                   ["fx", "agro"], 0.85)]),
]


def llm_calls(llm) -> int:
    return llm.calls if isinstance(llm, OpenAICompatibleLLMClient) else llm.call_count


def main() -> None:
    live = "--live" in sys.argv
    if live:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    llm = OpenAICompatibleLLMClient.from_env() if live else FakeLLMClient()
    runtime = build_branch(llm, StaticDirectory(COMPANIES), push_handler=lambda cid, dtos: print(
        f"  [SSE push] {cid}: {dtos[0].headline}"))

    print("=== Прогон 1 ===")
    for request in REQUESTS:
        runtime.branch.handle_banner(request)
    print(f"Вызовов LLM: {llm_calls(llm)}")

    for company in COMPANIES:
        print(f"\nТоп-3 для {company.company_id} ({company.segment_id}):")
        for dto in runtime.store.top_k(company.company_id, 3):
            flag = " [проверьте источник]" if dto.caveat else " [только новость]" if dto.raw_news else ""
            print(f"  {dto.label}: {dto.headline}{flag}\n    для вас: {dto.for_you or '-'}")

    calls_before = llm_calls(llm)
    print("\n=== Прогон 2 (те же данные) ===")
    for request in REQUESTS:
        runtime.branch.handle_banner(request)
    print(f"Новых вызовов LLM: {llm_calls(llm) - calls_before}; попаданий в кэш: {runtime.cache.hits}")
    if live:
        print(f"Токены: prompt={llm.prompt_tokens} completion={llm.completion_tokens}")


if __name__ == "__main__":
    main()
