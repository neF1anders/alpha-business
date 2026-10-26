# Обработка news_combined.jsonl и обновление БД

Все команды выполняются вручную из корня проекта в PowerShell; команды
`docker compose` без изменений работают и в bash. При подготовке
этого обновления выполнялись тесты с заглушкой модели и проверка `--dry-run`;
эмбеддинги новостей не вычислялись, данные в PostgreSQL не загружались.

## Состав файла и выбранная политика

Проверенный `data/raw/reports/news_combined.jsonl` содержит 47 268 записей:

| Вид содержимого | Записей | Подготовленный текст |
|---|---:|---|
| Полная статья | 5 869 | Текст статьи без изменений |
| Короткий анонс | 10 707 | Заголовок, пустая строка, текст анонса |
| Только метаданные, пустой `text` | 30 692 | Заголовок |

Источники: «Коммерсантъ», Интерфакс, «Ведомости», Банк России, Финмаркет и ТАСС.
Из прежних 5 878 статей присутствуют 5 869; ещё 9 отсутствуют. Импорт накопительный,
поэтому ранее загруженные отсутствующие статьи остаются в БД. Их повторная
подготовка из старого файла для этого не нужна.

Пользователь выбрал разметку всех материалов и разрешил retrieval по заголовкам
и анонсам. Для этого нужен **`--include-partial`**. Без него применяется прежний
строгий фильтр, и новые неполные записи исключаются.
Этот флаг не скачивает полные статьи и не восстанавливает отсутствующий текст.

Исходные `content_status`, `quality_flags`, `retrieval_eligible` сохраняются
в метаданных. Добавляется `source_metadata.ingestion` с полями:

- `content_kind`: `full_text`, `excerpt` или `title_only`;
- `partial_retrieval_allowed`: явный допуск неполного материала;
- `review_flags`: оставшиеся основания для ручной проверки.

Для `metadata_only` и `short_excerpt` флаг позволяет пропустить запрет сборщика
и следующие отметки: `article_not_fetched`, `not_full_article`, `title_only`,
`short_excerpt_not_full_article`, `excerpt_truncated`, `publication_time_minute_precision`.
Другие отметки, например `future_publication_date`, сохраняют `needs_review=true`.
Конфликтующие ID, дубли, неверные даты и некорректные записи проверяются как прежде.

## 1. Подготовить окружение

Запустите Docker Desktop. Сохраните существующий настроенный `.env`.
Проверьте наличие входного файла и очищенного CSV-словаря:

```powershell
Test-Path data/raw/reports/news_combined.jsonl
Test-Path data/processed/csv/companies.csv
docker compose up -d db
docker compose build ingest
docker compose run --rm ingest init-db
```

Сборка обязательна: в образ должны попасть новые параметры CLI.
`init-db` обновляет схему, сохраняя существующие записи.
Если `companies.csv` отсутствует, сначала выполните:

```powershell
docker compose run --rm ingest prepare-csv
```

CSV нужен классификатору для канонических названий стран и регионов. Повторный
`load-csv` для обновления только новостей не нужен. Для полностью новой БД
выполните также начальную настройку CSV/PDF из [README](../README.md).

## 2. Проверить входные данные без записи и эмбеддингов

```powershell
docker compose run --rm ingest prepare-news --source data/raw/reports/news_combined.jsonl --output data/processed/news-combined --include-partial --dry-run
```

Для проверенного файла ожидаются:

```text
input:        47268
accepted:     47268
rejected:         0
chunks:       52535
needs_review:     0
```

Эта команда не создаёт и не меняет артефакты, не запускает модель и не пишет в БД.
Это проверка подготовки; она не оценивает качество будущих тегов.

## 3. Подготовить новости и чанки

Выполните ту же команду без `--dry-run`:

```powershell
docker compose run --rm ingest prepare-news --source data/raw/reports/news_combined.jsonl --output data/processed/news-combined --include-partial
```

Результат: `data/processed/news-combined/*.news.json`, `manifest.json` и
`rejected.jsonl`. Здесь ещё нет тегов. Отдельная папка сохраняет прежние
артефакты `data/processed/news/` для сравнения. ID документа зависит от `news_id`,
поэтому смена имени входного файла сама по себе не создаёт второй документ в БД.

## 4. Разметить новости — здесь запускается модель

```powershell
docker compose run --rm ingest tag-chunks --news-only --news-source data/processed/news-combined --output data/processed/tagged-combined
```

**Именно эта команда вычисляет эмбеддинги для определения отрасли.** Она может
работать долго на CPU: нужно обработать до 52 535 чанков, часть из которых короткие.
Модель и кэш находятся в `data/cache/`; первый запуск требует скачивания модели.
Тексты обрабатываются локально. При нехватке памяти можно добавить `--batch-size 8`.

`--news-only` исключает PDF из обработки. Веса, пороги и правила тегов остаются
прежними: пять факторов, страны/регионы из CSV и сектор по близости к 19 описаниям.
Отрасль может остаться `null`; это допустимый результат для неоднозначных заголовков.

Кэш используется при совпадении текста и версии разметки. Поскольку версия
учитывает хэш файла классификатора, после обновления кода даже старые тексты
могут вычисляться заново. При прерывании повторите ту же команду: завершённые
пакеты текущей версии сохранены в SQLite-кэше.

Результат:

```text
data/processed/tagged-combined/
  news/          # .news.json с тегами и manifest.json
  report.json    # счётчики и распределение тегов
```

Теги находятся в `pages[].chunks[]`: `tags`, `factors`, `sector`, `countries`,
`regions`, `tagging_version`. Дождитесь успешного завершения команды перед импортом.
Кратко проверить отчёт можно так:

```powershell
$tagReport = Get-Content -Raw -Encoding utf8 data/processed/tagged-combined/report.json | ConvertFrom-Json
$tagReport.counts
```

## 5. Загрузить размеченные новости в PostgreSQL

```powershell
docker compose run --rm ingest load-news --format chunks --source data/processed/tagged-combined/news
```

Важны и `--source`, и `--format chunks`: это загрузчик размеченных документов
в `insights.documents`, `document_pages`, `document_chunks` и представление
`rag_chunks`. Другая ветка импорта `--format snapshot` предназначена для
аудированных снимков сборщика и таблицы `insights.news_articles`; здесь она не нужна.
Команда без `--source` загрузила бы старую папку `data/processed/tagged/news`.

Существующие статьи обновляются по стабильному ID; новые добавляются.
При неизменном тексте и параметрах разбиения ID чанков сохраняются. Изменённые
или исчезнувшие чанки обновляемого документа удаляются транзакционно.
Документы, отсутствующие в новом файле, автоматически не удаляются.
Повторный импорт тех же артефактов возвращает `unchanged`.
Обновление только новостей не требует `load-pdfs` или `load-csv`.

## 5а. Статьи для ленты и чата

Лента баннеров и чат читают `insights.news_articles`, а не `rag_chunks`.
Заголовки и анонсы туда пока не попадают: таблица хранит только полные тексты
(не короче 200 символов). Полные статьи из объединённого файла можно загрузить
так же, как из `news.jsonl`; уже загруженные статьи не дублируются:

```powershell
docker compose run --rm ingest load-news --source data/raw/reports/news_combined.jsonl
```

Для текущего файла это 5 869 статей, все они уже есть в `news.jsonl`, поэтому
команда добавит 0 записей. Неполные материалы доступны через `rag_chunks`
и CLI-поиск; подключение их к ленте с пометкой «по анонсу» — отдельная задача.

## 6. Проверить результат

```powershell
docker compose exec db psql -U insights -d insights
```

Внутри `psql` выполните:

```sql
-- Только документы из текущего объединённого файла:
SELECT count(DISTINCT d.document_id) AS documents, count(*) AS chunks,
       count(*) FILTER (WHERE c.tagging_version IS NOT NULL) AS tagged
FROM insights.documents d
JOIN insights.document_chunks c USING (document_id)
WHERE d.source_type = 'news'
  AND d.source_metadata->>'input_file' = 'news_combined.jsonl';

-- Распределение принятых документов по полноте:
SELECT source_metadata->'ingestion'->>'content_kind' AS content_kind, count(*)
FROM insights.documents
WHERE source_type = 'news'
  AND source_metadata->>'input_file' = 'news_combined.jsonl'
GROUP BY 1;

-- Проверка допуска в retrieval:
SELECT count(*) AS available_combined_chunks
FROM insights.rag_chunks c
JOIN insights.documents d USING (document_id)
WHERE d.source_metadata->>'input_file' = 'news_combined.jsonl';

-- Пример выдачи с видимой отметкой полноты:
SELECT c.title, c.text, c.tags, c.source_url,
       d.source_metadata->'ingestion'->>'content_kind' AS content_kind
FROM insights.rag_chunks c
JOIN insights.documents d USING (document_id)
WHERE c.tags @> ARRAY['country:КИТАЙ']::text[]
ORDER BY c.published_at DESC NULLS LAST, c.chunk_id
LIMIT 5;
```

В первом запросе ожидаются 47 268 документов, 52 535 чанков, все размечены.
В третьем — 52 535 доступных чанков для данного снимка. Общий объём БД больше,
если там уже есть PDF и 9 отсутствующих в новом файле старых статей.
Для выхода из `psql` введите `\q`. Затем можно проверить CLI-поиск:

```powershell
docker compose run --rm ingest search "нефть" --tag "country:КИТАЙ" --limit 3
```

Коллегам, формирующим ответ RAG, следует передавать вместе с текстом
`content_kind`, чтобы заголовок или анонс не выдавался за полный текст статьи.
Если нужен поиск только по полным текстам, добавьте условие
`d.source_metadata->'ingestion'->>'content_kind' = 'full_text'` к запросу с JOIN.

## Тесты без эмбеддингов и доступа к рабочей БД

```powershell
docker compose run --rm --no-deps --entrypoint python ingest -m unittest discover -s tests -p test_news_tagging.py -v
```

Классификатор в тестах заменён заглушкой; модель не загружается и эмбеддинги
реальных новостей не вычисляются. Проверяются неполные материалы, флаги качества,
стабильность ID, отсутствие записи при dry-run, разметка заголовков/анонсов,
кэш и сохранность PDF-манифеста при `--news-only`.
