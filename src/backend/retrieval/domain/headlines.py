"""News -> event cluster: scheduled macro events by headline topic and day, otherwise equal headlines."""
from datetime import datetime, timedelta, timezone
import hashlib
import re

# Scheduled releases produce many articles on one day (decision, statement, comments). A business client
# needs one banner per release, so a headline on such a topic joins the day's event. Measured on the news
# set: text similarity (lexical or E5) does not separate one event from one topic, and loose headline
# matching merges different companies ("X не выплатит дивиденды"), so other news stay one per headline.
SCHEDULED_EVENTS = {
    # The rate is set at the Bank of Russia board meeting: its statement names the meeting, not the rate.
    'policy_rate': re.compile(r'ключев\w* ставк|ставк\w* (?:цб|банка россии)|денежно-кредитн\w* политик'
                              r'|заседани\w* совета директоров (?:цб|банка россии)'),
    'inflation': re.compile(r'инфляц|дефляц|потребительск\w* цен'),
}
MOSCOW = timezone(timedelta(hours=3))


def _normalized(title: str) -> str:
    return ' '.join(re.findall(r'\w+', title.casefold().replace('ё', 'е')))


def headline_cluster_id(title: str, published_at: datetime) -> str:
    """Equal headlines on the same UTC day share a cluster."""
    key = _normalized(title) + '|' + published_at.astimezone(timezone.utc).date().isoformat()
    return 'headline:' + hashlib.sha256(key.encode()).hexdigest()[:24]


def event_cluster_id(title: str, published_at: datetime) -> str:
    """Stable across requests and companies: the summary cache and raw-banner replacement key on it."""
    normalized = _normalized(title)
    for topic, pattern in SCHEDULED_EVENTS.items():
        if pattern.search(normalized):
            return f'event:{topic}:{published_at.astimezone(MOSCOW).date().isoformat()}'
    return headline_cluster_id(title, published_at)
