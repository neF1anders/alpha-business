"""Deterministic monitoring topics from source activity, never invented exposure."""
from dataclasses import dataclass

from orchestrator.domain.news_quality import historical_retrospective

# These are questions to monitor, not assertions about the company's contracts.
TOPICS = {
    'trade': ('предложение товаров, оптовые цены и спрос',
              'налоги, маркировка и правила торговли',
              'стоимость закупок, хранения и доставки товаров'),
    'transport': ('тарифы, стоимость грузовых перевозок и транспортных услуг',
                  'правила перевозок, оформление грузов и таможенные процедуры',
                  'стоимость труда и доступность транспортной инфраструктуры'),
    'construction': ('спрос, цены и продажи жилых и нежилых зданий',
                     'стоимость строительных материалов и работ',
                     'строительные регламенты, закупки и финансирование проектов'),
    'energy': ('спрос и цены на электроэнергию',
               'тарифы и регулирование производства электроэнергии',
               'стоимость топлива, оборудования и ремонтов'),
    'it_telecom': ('спрос на программное обеспечение и цифровые услуги',
                   'регулирование программного обеспечения и обработки данных',
                   'стоимость разработки, труда и вычислительной инфраструктуры'),
}
GENERAL = ('изменения спроса и цен', 'изменения регулирования и налогов',
           'изменения стоимости ресурсов, труда и поставок')


def monitoring_queries(profile):
    activity=' '.join(profile.activity.split())
    if not activity or len(activity)>1200:
        raise ValueError('Confirmed company activity of 1..1200 chars required')
    sectors=sorted(t.split(':',1)[1] for t in profile.tags if t.startswith('sector:'))
    topics=TOPICS.get(sectors[0],GENERAL) if len(sectors)==1 else GENERAL
    return tuple(activity+'. '+topic+'.' for topic in topics)



@dataclass(frozen=True)
class ContextQuery:
    kind: str
    text: str
    source_tags: tuple[str, ...]


def profile_context_queries(profile):
    """Monitoring hypotheses from source profile facts, without inferred contracts.

    One channel per economic mechanism, regardless of the number of products or
    countries. No activity prefix: it would bias macroeconomic searches toward
    industry news again. No claim of floating rates or foreign-currency debt.
    """
    queries = []
    rate_tags = tuple(sorted(profile.tags & {
        'product:credit', 'product:leasing', 'product:factoring', 'product:deposit'}))
    if rate_tags:
        queries.append(ContextQuery('interest_rates',
            'Решения Банка России по ключевой ставке, стоимость кредитования '
            'и доходность банковских депозитов.', rate_tags))
    country_tags = tuple(sorted(t for t in profile.tags if t.startswith('country:') and t[8:].strip()))
    if country_tags:
        queries.append(ContextQuery('international_settlements',
            'Изменения курса рубля к иностранным валютам, валютные расчеты '
            'и условия внешней торговли.', country_tags))
    return tuple(queries)


def balanced_candidates(rankings,limit):
    if type(limit) is not int or limit<1:raise ValueError('Positive candidate limit required')
    selected=[];seen=set()
    for pos in range(max(map(len,rankings),default=0)):
        for ranking in rankings:
            if pos<len(ranking) and ranking[pos] not in seen:
                selected.append(ranking[pos]);seen.add(ranking[pos])
                if len(selected)==limit:return selected
    return selected
