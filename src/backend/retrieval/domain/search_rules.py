"""Tag -> full-text search rules. Explicit lexical baseline, not a trained classifier."""
import re

# Shared tag vocabulary: docs/tags.md. Each phrase is a literal FTS phrase.
TAG_PHRASES = {
    'factor:key_rate': ('ключевая ставка', 'ставка ЦБ', 'денежно-кредитная политика', 'стоимость кредитов'),
    'factor:fx': ('курс рубля', 'курс доллара', 'курс юаня', 'валютный рынок', 'валютные платежи'),
    'factor:labor': ('рынок труда', 'дефицит кадров', 'заработная плата', 'безработица'),
    'factor:commodities': ('цена нефти', 'цены на нефть', 'цены на металлы', 'сталь', 'зерно', 'золото'),
    'factor:consumer_demand': ('потребительский спрос', 'розничные продажи', 'потребительские расходы'),
    'factor:construction_market': ('новостройки', 'ипотека', 'жилищное строительство', 'застройщики'),
    'sector:agro': ('сельское хозяйство', 'аграрный', 'урожай', 'зерно'),
    'sector:mining': ('добыча', 'горнодобывающий', 'рудник'),
    'sector:manufacturing': ('промышленность', 'производство', 'завод', 'металлургия'),
    'sector:energy': ('энергетика', 'электроэнергия', 'электростанция'),
    'sector:utilities': ('ЖКХ', 'коммунальные услуги', 'водоснабжение'),
    'sector:construction': ('строительство', 'застройщики', 'стройматериалы'),
    'sector:trade': ('ритейл', 'розничная торговля', 'магазин', 'маркетплейс'),
    'sector:transport': ('грузоперевозки', 'логистика', 'железная дорога', 'авиаперевозки'),
    'sector:horeca': ('ресторан', 'гостиница', 'общепит', 'отель'),
    'sector:it_telecom': ('телеком', 'связь', 'интернет', 'программное обеспечение', 'дата-центр'),
    'sector:finance': ('банк', 'страхование', 'кредитование', 'финансовый рынок'),
    'sector:real_estate': ('недвижимость', 'аренда', 'жилье'),
    'sector:prof_services': ('консалтинг', 'аудит', 'юридические услуги'),
    'sector:admin_services': ('аутсорсинг', 'кадровое агентство', 'клининг'),
    'sector:public': ('госзакупки', 'государственный бюджет'),
    'sector:education': ('образование', 'университет', 'школа'),
    'sector:health': ('здравоохранение', 'лекарство', 'клиника', 'фармацевтика'),
    'sector:culture_sport': ('кинотеатр', 'театр', 'спорт', 'музей'),
    'sector:other_services': ('ремонт техники', 'бытовые услуги'),
}


def phrase_query(phrases):
    # Strip operators from external geography tags, rather than treating them as FTS syntax.
    cleaned = [' '.join(re.findall(r'[\w]+', phrase, flags=re.UNICODE)) for phrase in phrases]
    return ' OR '.join('"'+value+'"' for value in cleaned if value)


# Explicit geography aliases, never a Russian stem (Дания and данные both stem to дан).
GEO_ALIASES = {
    'дания': ('дания', 'дании', 'данию', 'данией'),
    'китай': ('китай', 'китая', 'китае', 'китаю', 'китаем'),
    'россия': ('россия', 'россии', 'россию', 'россией'),
    'москва': ('москва', 'москвы', 'москве', 'москву', 'москвой'),
    'санкт-петербург': ('санкт-петербург', 'санкт-петербурга', 'санкт-петербурге', 'санкт-петербургу', 'санкт-петербургом'),
    'южная корея': ('южная корея', 'южной кореи', 'южной корее', 'южную корею', 'южной кореей'),
}


def geography_pattern(value):
    value = ' '.join(value.casefold().replace('ё','е').split())
    forms = GEO_ALIASES.get(value, (value,))
    # PostgreSQL ARE word boundaries; escaping makes external profile values literal.
    return r'\m(?:' + '|'.join(re.escape(f) for f in forms) + r')\M'


def rules_for(tags):
    rules = dict(TAG_PHRASES)
    for tag in tags:
        namespace, _, value = tag.partition(':')
        if namespace in ('country', 'region') and len(value.strip()) >= 3:
            rules[tag] = (value,)
    return [{'tag': tag, 'query': phrase_query(phrases),
             'geo_pattern': geography_pattern(tag.partition(':')[2])
             if tag.startswith(('country:', 'region:')) else None}
            for tag, phrases in sorted(rules.items())]
