"""Настройки из переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    tags_path: str = "data/processed/company_tags.csv"
    reports_path: str = "data/raw/reports"
    banner_slo_s: float = 2.0
    chat_slo_s: float = 6.0
    news_search_mode: str = "lexical"
    news_candidate_limit: int = 80

    def __post_init__(self):
        if type(self.news_candidate_limit) is not int or not 1 <= self.news_candidate_limit <= 100:
            raise ValueError("NEWS_CANDIDATE_LIMIT must be an integer in 1..100")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            news_candidate_limit=int(os.environ.get("NEWS_CANDIDATE_LIMIT", cls.news_candidate_limit)),
            news_search_mode=os.environ.get("NEWS_SEARCH_MODE", cls.news_search_mode),
            tags_path=os.environ.get("COMPANY_TAGS_PATH", cls.tags_path),
            reports_path=os.environ.get("REPORTS_PATH", cls.reports_path),
            banner_slo_s=float(os.environ.get("BANNER_SLO_S", cls.banner_slo_s)),
            chat_slo_s=float(os.environ.get("CHAT_SLO_S", cls.chat_slo_s)),
        )
