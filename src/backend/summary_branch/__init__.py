"""Ветка «кластер новостей → карта влияния → пути влияния на компанию → персональный баннер или ответ чата».

Точка входа для бэка: `build_branch(...)`, затем `runtime.branch`.
"""
from .bootstrap import BranchRuntime, build_branch  # noqa: F401
from .config import BranchConfig  # noqa: F401
from .domain.errors import BranchError, BudgetExhausted, SummarizationFailed  # noqa: F401
from .domain.models import *  # noqa: F401,F403
from .domain.ports import BannerStore, LLMClient, SummaryBranch, SummaryCache  # noqa: F401
