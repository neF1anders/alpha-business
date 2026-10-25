"""Настройки из переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    tags_path: str = "data/processed/company_tags.csv"
    reports_path: str = "data/raw/reports"
    banner_slo_s: float = 3.5                        # SLI выдачи баннеров: p95 до 4 с, остаток уходит на сеть
    chat_slo_s: float = 6.0
    news_search_mode: str = "lexical"
    news_candidate_limit: int = 80
    llm_mode: str = ""                               # gateway | fake | template; пусто — gateway при API_KEY, иначе fake
    banner_store_path: str = "data/cache/banners.db"
    demo_company_id: str = ""                        # клиент фронта по умолчанию (`demo-company`)
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")

    def __post_init__(self):
        if type(self.news_candidate_limit) is not int or not 1 <= self.news_candidate_limit <= 100:
            raise ValueError("NEWS_CANDIDATE_LIMIT must be an integer in 1..100")
        if self.llm_mode not in ("", "gateway", "fake", "template"):
            raise ValueError("LLM_MODE must be gateway, fake or template")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            news_candidate_limit=int(os.environ.get("NEWS_CANDIDATE_LIMIT", cls.news_candidate_limit)),
            news_search_mode=os.environ.get("NEWS_SEARCH_MODE", cls.news_search_mode),
            tags_path=os.environ.get("COMPANY_TAGS_PATH", cls.tags_path),
            reports_path=os.environ.get("REPORTS_PATH", cls.reports_path),
            banner_slo_s=float(os.environ.get("BANNER_SLO_S", cls.banner_slo_s)),
            chat_slo_s=float(os.environ.get("CHAT_SLO_S", cls.chat_slo_s)),
            llm_mode=os.environ.get("LLM_MODE", cls.llm_mode),
            banner_store_path=os.environ.get("BANNER_STORE_PATH", cls.banner_store_path),
            demo_company_id=os.environ.get("DEMO_COMPANY_ID", cls.demo_company_id),
            cors_origins=tuple(o.strip() for o in os.environ["CORS_ORIGINS"].split(",") if o.strip())
            if "CORS_ORIGINS" in os.environ else cls.cors_origins,
        )

    def resolved_llm_mode(self) -> str:
        return self.llm_mode or ("gateway" if os.environ.get("API_KEY") else "fake")
