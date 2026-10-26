"""Досье компании из таблиц `insights.companies`, `insights.payments`, `insights.person_links` (только чтение).

Платёжный граф: поставщики — кому компания платит, покупатели — кто платит ей. Переводы компаниям своей группы
и компаниям, связанным через общих физлиц, исключаются: это переводы «себе», они не говорят о рынке.
Контрагент без профиля остаётся в сумме потока, но не даёт отрасли. Наружу уходят только отрасли и доли.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from retrieval.infrastructure.connection import open_connection

from ..domain.models import CompanyDossier, CounterpartySector

log = logging.getLogger(__name__)

TOP_SECTORS = 5
TOP_AFFILIATE_SECTORS = 5
LINKED_LIMIT = 2000                 # самые большие группы в данных — около 600 компаний
_NAME_MAX = 60
_PRODUCT_COLUMNS = {"cl_product_rko_mark": "rko", "cl_product_gar_mark": "guarantee", "cl_product_loa_mark": "loan",
                    "cl_product_lea_mark": "leasing", "cl_product_dep_mark": "deposit", "cl_product_zpp_mark": "payroll",
                    "factoring_mark": "factoring"}
_REVENUE_PLACEHOLDER = 1000

PROFILE_SQL = """
SELECT mainokved_code, mainokved_name, region, city, cl_type, fts_flag, fts_country_name, revenue_company,
       amount_workers_fns_gradation, eco_mark_label, company_age_years_gradation, negativemark, group_id,
       cl_product_rko_mark, cl_product_gar_mark, cl_product_loa_mark, cl_product_lea_mark, cl_product_dep_mark,
       cl_product_zpp_mark, factoring_mark
FROM insights.companies WHERE synthetic_u = %(id)s
"""

LINKED_CTE = """
WITH linked AS (
    SELECT b.synthetic_u AS company_id
    FROM insights.person_links a
    JOIN insights.person_links b ON b.synthetic_f = a.synthetic_f AND b.synthetic_u <> a.synthetic_u
    WHERE a.synthetic_u = %(id)s
    UNION
    SELECT synthetic_u FROM insights.companies
    WHERE %(group)s::text IS NOT NULL AND group_id = %(group)s AND synthetic_u <> %(id)s
    LIMIT %(linked_limit)s
)
"""

AFFILIATES_SQL = LINKED_CTE + """
SELECT left(c.mainokved_code, 2) AS division, count(*) AS companies
FROM linked l JOIN insights.companies c ON c.synthetic_u = l.company_id
WHERE c.mainokved_code IS NOT NULL
GROUP BY 1 ORDER BY companies DESC, division LIMIT %(top)s
"""

FLOWS_SQL = LINKED_CTE + """
, flows AS (
    SELECT 'supplier' AS side, beneficiary AS counterparty, pay_amt FROM insights.payments WHERE payer = %(id)s
    UNION ALL
    SELECT 'buyer', payer, pay_amt FROM insights.payments WHERE beneficiary = %(id)s
), external AS (
    SELECT f.* FROM flows f WHERE NOT EXISTS (SELECT 1 FROM linked l WHERE l.company_id = f.counterparty)
)
SELECT e.side, left(c.mainokved_code, 2) AS division,
       mode() WITHIN GROUP (ORDER BY c.mainokved_name) AS name,
       sum(e.pay_amt) AS amount, count(*) AS counterparties,
       coalesce(sum(e.pay_amt) FILTER (WHERE c.fts_flag), 0) AS foreign_amount,
       sum(sum(e.pay_amt)) OVER (PARTITION BY e.side) AS side_total,
       max(max(e.pay_amt)) OVER (PARTITION BY e.side) AS side_top
FROM external e LEFT JOIN insights.companies c ON c.synthetic_u = e.counterparty
GROUP BY e.side, left(c.mainokved_code, 2)
"""


class PostgresDossierSource:
    """Реализует CompanyDossierSource. Запросы идут по первичному ключу и индексам, без изменения схемы."""

    def __init__(self, *, max_concurrency: int = 4) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def get(self, company_id: str) -> Optional[CompanyDossier]:
        async with self._semaphore:
            conn = await open_connection()
            try:
                async with conn.cursor() as cur:
                    await cur.execute("SET TRANSACTION READ ONLY")
                    await cur.execute(PROFILE_SQL, {"id": company_id})
                    row = await cur.fetchone()
                    if row is None:
                        return None
                    params = {"id": company_id, "group": row["group_id"], "linked_limit": LINKED_LIMIT,
                              "top": TOP_AFFILIATE_SECTORS}
                    await cur.execute(AFFILIATES_SQL, params)
                    affiliates = await cur.fetchall()
                    await cur.execute(FLOWS_SQL, params)
                    flows = await cur.fetchall()
            finally:
                await conn.close()
        return build_dossier(row, affiliates, flows)


def build_dossier(row: dict, affiliates: list[dict], flows: list[dict]) -> CompanyDossier:
    suppliers, top_supplier = _sides(flows, "supplier")
    buyers, top_buyer = _sides(flows, "buyer")
    revenue = float(row["revenue_company"]) if row.get("revenue_company") is not None else None
    countries = tuple(c.strip() for c in (row.get("fts_country_name") or "").split(",") if c.strip())
    return CompanyDossier(
        okved_code=row.get("mainokved_code") or "", okved_name=row.get("mainokved_name") or "",
        region=row.get("region") or "", city=row.get("city") or "", client_type=(row.get("cl_type") or "").lower(),
        products=frozenset(name for column, name in _PRODUCT_COLUMNS.items() if row.get(column)),
        trade_countries=countries, foreign_trade=bool(row.get("fts_flag")) or bool(countries),
        revenue=None if revenue == _REVENUE_PLACEHOLDER else revenue,
        workers_band=row.get("amount_workers_fns_gradation") or "", lifecycle=row.get("eco_mark_label") or "",
        age_band=row.get("company_age_years_gradation") or "", negative_flag=bool(row.get("negativemark")),
        group_id=row.get("group_id") or "", suppliers=suppliers, buyers=buyers,
        top_supplier_share=top_supplier, top_buyer_share=top_buyer,
        affiliate_sectors=tuple(a["division"] for a in affiliates if a.get("division")))


def _sides(flows: list[dict], side: str) -> tuple[tuple[CounterpartySector, ...], float]:
    """Отрасли одной стороны потока по убыванию доли и доля крупнейшего контрагента."""
    rows = [r for r in flows if r["side"] == side]
    if not rows:
        return (), 0.0
    total = float(rows[0]["side_total"]) or 1.0
    sectors = sorted((CounterpartySector(division=r["division"], name=(r["name"] or "")[:_NAME_MAX],
                                         share=round(float(r["amount"]) / total, 4),
                                         counterparties=int(r["counterparties"]),
                                         foreign_trade_share=round(float(r["foreign_amount"]) / float(r["amount"]), 4)
                                         if r["amount"] else 0.0)
                      for r in rows if r["division"]), key=lambda s: s.share, reverse=True)
    return tuple(sectors[:TOP_SECTORS]), round(float(rows[0]["side_top"]) / total, 4)
