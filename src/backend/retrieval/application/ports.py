"""Ports of the retrieval application layer; implemented in infrastructure/."""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence

Row = dict[str, Any]


class NewsSearchSession(Protocol):
    """One read-only database session shared by a news query and its bank context."""

    async def fetch_news(self, *, rules: list[dict], search: str, geo_pattern: Optional[str],
                         geo_search: str, as_of: datetime, since: datetime, candidate_limit: int,
                         tags: list[str], tag_count: int, chat: bool) -> list[Row]: ...

    async def detect_topics(self, *, title: str, lead: str, rules: list[dict]) -> list[Row]: ...

    async def fetch_bank_chunks(self, *, query: str, anchor: str, as_of: date, since: date,
                                limit: int) -> list[Row]: ...


class NewsSearchStore(Protocol):
    def session(self, statement_timeout_ms: int) -> AbstractAsyncContextManager[NewsSearchSession]: ...


class DocumentSearchStore(Protocol):
    def search_chunks(self, *, query: str, natural: bool, as_of: date, since: Optional[date],
                      sources: Optional[Sequence[str]], undated: bool,
                      per_document: Optional[int], limit: int) -> list[Row]: ...


class ReportFileCatalog(Protocol):
    def has_pdf(self, root: Path, source_file: str) -> bool: ...


class ActivityCatalog(Protocol):
    def load_activities(self) -> dict[str, str]: ...


class TextEncoder(Protocol):
    """Dense model: windowed token passages for documents, one vector for a query."""
    device: str

    def passages(self, title: str, text: str) -> list[tuple[list[int], int, int]]: ...
    def encode_ids(self, sequences: list[list[int]]) -> Any: ...
    def query(self, text: str) -> Any: ...


class PairScorer(Protocol):
    def token_count(self, text: str) -> int: ...
    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]: ...


class EmbeddingStore(Protocol):
    def ensure_space(self, space_id: str, recipe: dict) -> None: ...
    def read_news(self) -> list[tuple]: ...
    def read_indexed_summary(self, space_id: str) -> list[tuple]: ...
    def save_group(self, space_id: str, group: list[tuple], metadata: list[tuple], vectors: Any) -> None: ...
    def load_snapshot(self, space_id: str) -> tuple[dict[str, Row], list[tuple]]: ...
