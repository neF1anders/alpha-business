"""Source-backed company activity (OKVED name) from the insights.companies table."""
from typing import Optional

from ingestion.infrastructure import postgres


class PostgresActivityCatalog:
    def load_activities(self) -> dict[str, str]:
        with postgres.connect() as c:
            c.execute('SET TRANSACTION READ ONLY')
            c.execute("SET LOCAL statement_timeout='5s'")
            return dict(c.execute('SELECT synthetic_u,mainokved_name FROM insights.companies'))

    def activity_of(self, company_id: str) -> Optional[str]:
        with postgres.connect() as c:
            c.execute('SET TRANSACTION READ ONLY')
            row=c.execute('SELECT mainokved_name FROM insights.companies WHERE synthetic_u=%s',(company_id,)).fetchone()
        return None if row is None else row[0]
