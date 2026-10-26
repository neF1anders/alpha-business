"""S8: сборка BannerDTO и отправка новых баннеров подписчику."""
from __future__ import annotations

from typing import Callable

from ..config import BranchConfig
from ..domain.models import Banner, BannerDTO, ClusterSummary, Decision

PushHandler = Callable[[str, list[BannerDTO]], None]


class DefaultPublisher:
    def __init__(self, config: BranchConfig | None = None, push_handler: PushHandler | None = None) -> None:
        self._config = config or BranchConfig()
        self._push_handler = push_handler

    def to_dto(self, banner: Banner, seg: ClusterSummary, placements: list[str] | None = None) -> BannerDTO:
        label = self._label(banner)
        cluster_id = seg.key.cluster_id
        if banner.decision == Decision.SHOW_RAW_NEWS:
            lead_source = seg.sources[:1]  # первый источник — главная новость
            return BannerDTO(
                banner_id=banner.banner_id, cluster_id=cluster_id, severity=banner.severity, label=label,
                headline=lead_source[0].title if lead_source else seg.headline, for_you="",
                caveat=False, raw_news=True, sources=lead_source, expires_at=banner.expires_at,
            )
        return BannerDTO(
            banner_id=banner.banner_id, cluster_id=cluster_id, severity=banner.severity, label=label,
            headline=banner.headline or seg.headline, for_you=banner.for_you,
            caveat=banner.decision == Decision.SHOW_WITH_CAVEAT, raw_news=False,
            sources=list(seg.sources), expires_at=banner.expires_at, placements=list(placements or []),
        )

    def push(self, company_id: str, banners: list[BannerDTO]) -> None:
        if self._push_handler is not None:
            self._push_handler(company_id, banners)

    def _label(self, banner: Banner) -> str:
        severity = self._config.severity_labels_ru.get(banner.severity.value, banner.severity.value)
        topic = self._config.topic_labels_ru.get(banner.topic)
        return f"{severity} · {topic}" if topic else severity   # тема вне словаря не показывается служебным кодом
