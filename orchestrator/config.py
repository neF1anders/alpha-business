"""Настройки из переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    tags_path: str = "data/processed/company_tags.csv"
    banner_slo_s: float = 2.0
    chat_slo_s: float = 6.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            tags_path=os.environ.get("COMPANY_TAGS_PATH", cls.tags_path),
            banner_slo_s=float(os.environ.get("BANNER_SLO_S", cls.banner_slo_s)),
            chat_slo_s=float(os.environ.get("CHAT_SLO_S", cls.chat_slo_s)),
        )
