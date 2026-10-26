"""Настройки ветки. Пороги уверенности и веса остаются в scoring.py."""
from __future__ import annotations

from pydantic import BaseModel, Field


class BranchConfig(BaseModel):
    max_clusters_per_request: int = Field(default=3, ge=1)
    top_k_serve: int = Field(default=3, ge=1)
    max_parallel_clusters: int = Field(default=3, ge=1)
    max_generation_attempts: int = Field(default=2, ge=1)   # попытки генерации баннера при полном бюджете
    chat_generation_attempts: int = Field(default=1, ge=1)  # в чате повтор удваивает задержку, а SLO 4 с
    min_grounding: float = Field(default=0.6, ge=0, le=1)    # ниже саммари считается невалидным
    cache_ttl_s: int = Field(default=168 * 3600, gt=0)   # не дольше наибольшего banner_ttl_h
    banner_ttl_h: dict[str, int] = Field(
        default_factory=lambda: {"rate": 168, "fx": 36, "labor": 120, "demand": 120})   # по срокам актуальности, eval/results/REPORT.md
    banner_ttl_default_h: int = Field(default=36, gt=0)
    news_text_max_chars: int = Field(default=2000, gt=0)
    quote_max_chars: int = Field(default=200, gt=0)
    max_sources: int = Field(default=3, gt=0)
    prompt_version: str = "v1"
    banner_store_path: str | None = None   # файл SQLite для баннеров; None — только ОЗУ
    topic_labels_ru: dict[str, str] = Field(default_factory=lambda: {
        "rate": "ставка", "fx": "курс", "labor": "рынок труда", "demand": "спрос",
    })
    severity_labels_ru: dict[str, str] = Field(default_factory=lambda: {
        "attention": "Внимание", "watch": "Наблюдение", "opportunity": "Возможность",
    })

    def ttl_hours(self, topic: str) -> int:
        return self.banner_ttl_h.get(topic, self.banner_ttl_default_h)
