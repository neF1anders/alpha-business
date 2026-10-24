# Теги компаний

Формат `пространство:значение`. Источник: `scripts/build_tags.py` (из `profiles.csv`), результат `data/processed/company_tags.csv`.

## Общие с новостями (совпадение по равенству строк)

Классификатор новостей должен ставить новостям теги из этих же пространств.

| Пространство | Значения | Правило для компании |
|---|---|---|
| `factor` | `key_rate`, `fx`, `labor`, `commodities`, `consumer_demand`, `construction_market` | см. ниже |
| `sector` | `agro`, `mining`, `manufacturing`, `energy`, `utilities`, `construction`, `trade`, `transport`, `horeca`, `it_telecom`, `finance`, `real_estate`, `prof_services`, `admin_services`, `public`, `education`, `health`, `culture_sport`, `other_services` | диапазон двузначного кода ОКВЭД |
| `okved` | двузначный раздел, например `okved:46` | `okved_division` |
| `country` | как в данных ФТС, например `country:КИТАЙ` | страны из `fts_countries` |
| `region` | как в данных, например `region:Москва` | `region` |

Правила `factor` (эвристики, их стоит обсудить):

| Тег | Когда ставится |
|---|---|
| `factor:key_rate` | есть кредит, лизинг, факторинг или депозит |
| `factor:fx` | `fts_flag = 1` (есть сведения в ФТС) |
| `factor:labor` | численность 101-250 или более 251 |
| `factor:commodities` | сектор agro, mining, energy или разделы 10, 11, 19, 20, 23, 24, 25 |
| `factor:consumer_demand` | сектор trade, horeca, culture_sport, other_services |
| `factor:construction_market` | сектор construction или real_estate |

Страны ВЭД не доказывают направление импорта или экспорта, поэтому `factor:fx` и `country:*` означают только «есть внешнеторговые сведения».

## Только профиль (модификаторы для реранкера)

`product:*` (rko, guarantee, credit, leasing, deposit, payroll, factoring), `size:*` (large от 15 млрд, mid от 1 до 15 млрд, small, revenue_unknown),
`workers:*`, `stage:*` (из `eco_mark_label`), `age:*`, `client:*` (client, prospect, close_client), `group:large` (в группе от 10 компаний).

Теги от контрагентов (граф платежей) добавляются на шаге 3.
