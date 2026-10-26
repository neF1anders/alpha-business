"""UTC clock used for receipts and reports."""
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()
