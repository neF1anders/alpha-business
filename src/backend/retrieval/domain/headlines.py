"""Conservative duplicate suppression by normalised headline and publication day."""
from datetime import datetime, timezone
import hashlib
import re


def headline_cluster_id(title: str, published_at: datetime) -> str:
    """Not semantic event clustering: equal headlines on the same UTC day share a cluster."""
    normalized = ' '.join(re.findall(r'\w+', title.casefold().replace('ё', 'е')))
    key = normalized + '|' + published_at.astimezone(timezone.utc).date().isoformat()
    return 'headline:' + hashlib.sha256(key.encode()).hexdigest()[:24]
