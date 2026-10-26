"""Event -> bank context rules: topic anchors, profile hints and input validation."""
from datetime import datetime

from orchestrator.domain.models import CompanyProfile, NewsItem

# Narrow subjects anchor bank retrieval. Broad words such as 'рынок', 'банк',
# 'производство' cannot turn an unrelated bank report into event context.
TOPICS = {
    'policy_rate': ('ключевая ставка', 'денежно-кредитная политика', 'ставка ЦБ'),
    'inflation': ('инфляция', 'потребительские цены'),
    'labor': ('рынок труда', 'безработица', 'дефицит кадров', 'заработная плата'),
    'consumption': ('потребительский спрос', 'потребительские расходы', 'розничные продажи'),
    'fx': ('курс рубля', 'курс доллара', 'курс юаня', 'валютный рынок'),
    'gold': ('золото', 'золотодобыча'),
    'steel': ('сталь', 'металлургия', 'сталелитейный'),
    'housing': ('ипотека', 'новостройки', 'жилищное строительство'),
    'fish': ('рыболовство', 'вылов рыбы', 'вылов лосося', 'промысловая разведка'),
    'transport': ('грузоперевозки', 'грузовые перевозки', 'фрахт'),
    'datacenters': ('дата-центр', 'центр обработки данных', 'ЦОД'),
}
PROFILE_HINTS = {'product:credit': 'кредит', 'product:leasing': 'лизинг',
                 'product:factoring': 'факторинг', 'product:deposit': 'депозит'}
# Do not infer borrowing from sector/size or foreign-currency exposure from FTS.


def validate(event, profile, as_of, limit):
    if not isinstance(event, NewsItem) or not isinstance(profile, CompanyProfile):
        raise ValueError('NewsItem and CompanyProfile required')
    if not event.news_id or not event.title.strip() or len(event.title) > 1000 or not event.text.strip() or len(event.text) > 100000:
        raise ValueError('Nonempty event ID/title/text required; title <=1000, text <=100000')
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or event.published_at.tzinfo is None:
        raise ValueError('Timezone-aware timestamps required')
    if event.published_at > as_of:
        raise ValueError('Event is later than as_of')
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError('limit must be 1..5')
    if len(profile.tags) > 100 or any(not isinstance(t,str) or len(t)>128 for t in profile.tags):
        raise ValueError('At most 100 bounded company tags required')
