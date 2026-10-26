# Сборка и обслуживание БД

Перенесено из корневого README. Общий обзор проекта: [README](../README.md).

## Собрать БД с нуля после получения PR

Нужны Git и запущенный Docker Desktop в режиме Linux containers (на Linux — Docker
Engine с Compose). Python и Tesseract на компьютер устанавливать не нужно: они
включены в образ обработки. Для первой сборки нужен интернет для скачивания образов
и зависимостей. Команды ниже рассчитаны на PowerShell и выполняются из корня клона.
На Linux/macOS команды `docker compose` те же; `Get-Content` замените на `cat`,
`Test-Path`/`Get-ChildItem` — на `ls`. Краткая bash-последовательность — в [README](../README.md#быстрый-старт).

1. Получите ветку PR или обновите репозиторий после слияния. В клоне должны быть:

   Для нового клона замените значения в угловых скобках своими:

   ```powershell
   git clone <URL-репозитория> alpha-business
   Set-Location alpha-business
   git fetch origin
   git switch --track origin/<ветка-PR>
   ```

   Последняя команда нужна, если изменения ещё в отдельной ветке и локальной
   ветки с таким именем нет. После слияния используйте основную ветку проекта.
   Если репозиторий уже есть, перейдите в его папку, проверьте `git status`,
   переключитесь на нужную ветку и выполните `git pull --ff-only`.
   Локальные незакоммиченные изменения сначала сохраните.

   ```text
   data/raw/users/       3 CSV
   data/raw/reports/     9 PDF и news.jsonl
   data/reports-metadata.json
   ```

   Исходные данные и метаданные должны войти в PR. Папка `data/processed/` не входит:
   она будет создана автоматически. Если исходных файлов нет, получите их из ветки PR
   до запуска скриптов.

   Проверить наличие файлов можно так:

   ```powershell
   Get-ChildItem data/raw/users -Filter *.csv
   Get-ChildItem data/raw/reports -Filter *.pdf
   Test-Path data/raw/reports/news.jsonl
   Test-Path data/reports-metadata.json
   ```

   Две последние команды должны вернуть `True`. Пустой `data/processed/`
   после клонирования — ожидаемое состояние. Чужая локальная БД и `.env`
   через Git не передаются.

2. Проверьте Docker:

   ```powershell
   docker version
   docker compose version
   ```

   В `docker version` должны присутствовать сведения о Client и Server. Ошибка
   подключения к Server означает, что Docker Desktop/движок ещё не запущен.

3. Создайте собственные настройки (существующий настроенный `.env` не перезаписывайте):

   ```powershell
   Copy-Item .env.example .env
   ```

   Откройте `.env`, задайте свой `POSTGRES_PASSWORD` и свободный `POSTGRES_PORT`.
   Если Windows запрещает занять `5432`, попробуйте `5433` или `15432`.
   Внутренний порт БД остаётся `5432`; настройки `ingest` менять не нужно.
   Пароль должен отличаться от `replace-with-local-password` из шаблона.
   `.env` лежит рядом с `compose.yaml`; Compose читает его автоматически.

4. Запустите PostgreSQL, соберите обработчик и создайте таблицы:

   ```powershell
   docker compose up -d db
   docker compose ps
   docker compose build ingest
   docker compose run --rm ingest init-db
   ```

   Сервис `db` запускает сервер PostgreSQL и хранит данные в постоянном volume.
   Сервис `ingest` выполняет одну команду обработки и завершается. Флаг `--rm`
   удаляет завершившийся контейнер обработчика; файлы в `data/` и БД сохраняются.
   Папка `data/` подключена внутрь обработчика как `/app/data`, поэтому результаты
   сразу видны в IDE. `init-db` создаёт/обновляет схему, но сам данные не загружает.

5. Подготовьте и загрузите CSV:

   ```powershell
   docker compose run --rm ingest prepare-csv
   Get-Content -Encoding utf8 data/processed/csv/manifest.json
   docker compose run --rm ingest load-csv
   ```

   Исключаются конфликтные профили и связи с ними, переводы самим себе и дубли.
   Все исключения сохраняются в `data/processed/csv/rejected.jsonl`. Контрагенты
   без карточки сохраняются как сущности без профиля.

6. Подготовьте PDF и новости JSONL, разметьте чанки и загрузите их в БД:

   ```powershell
   docker compose run --rm ingest extract-pdfs --metadata data/reports-metadata.json
   Get-Content -Encoding utf8 data/processed/pdf/report.json
   docker compose run --rm ingest prepare-news
   docker compose run --rm ingest tag-chunks
   Get-Content -Encoding utf8 data/processed/tagged/report.json
   docker compose run --rm ingest load-pdfs
   docker compose run --rm ingest load-news
   docker compose run --rm ingest load-news --source data/raw/reports/news.jsonl
   docker compose run --rm --entrypoint python ingest -m scripts.build_tags --from-db
   ```

   Две последние команды нужны продукту. Первая из них загружает полные статьи
   в `insights.news_articles` — эту таблицу читают лента баннеров и чат (`load-news`
   без `--source` заполняет только чанки `rag_chunks`). Для файла формат
   определяется автоматически (`--format jsonl`): берутся записи с
   `retrieval_eligible=true`, анонсы и заголовки пропускаются, импорт помечается
   как непроверенный (`raw_receipt.kind=collector_jsonl`), потому что HTML-квитанций
   источника в репозитории нет. Аудированный снимок сборщика по-прежнему грузится
   через `--format snapshot`. Повторный запуск возвращает `unchanged`.
   Вторая собирает `data/processed/company_tags.csv` — теги компаний для оркестратора.

   OCR 64 страниц может занять несколько минут. JSON в `data/processed/pdf/` —
   промежуточный результат. `prepare-news` читает `news.jsonl` без OCR.
   `tag-chunks` размечает фрагменты обоих источников и сохраняет их в
   `data/processed/tagged/pdf/` и `data/processed/tagged/news/`.
   Команды `load-pdfs` и `load-news` по умолчанию читают именно эти папки.
   При первом запуске разметки скачивается модель эмбеддингов (несколько сотен МБ);
   модель и результаты кэшируются в `data/cache/`. Все тексты обрабатываются локально.
   Разметка всего набора на CPU может занять десятки минут. Если она прервалась,
   повторите `tag-chunks`: уже рассчитанные результаты берутся из кэша.
   При прерывании распознавания повторите `extract-pdfs`: готовые страницы берутся
   из кэша. Проверьте выборочно текст и числа по оригинальным PDF.

7. Проверьте, что документы загружены и доступны для поиска:

   ```powershell
   docker compose exec db psql -U insights -d insights -c "SELECT source_type, count(*) AS documents, sum(page_count) AS sections FROM insights.documents GROUP BY source_type;"
   docker compose run --rm ingest search "ключевая ставка"
   docker compose run --rm ingest search "Китай" --tag "country:КИТАЙ" --tag "factor:fx"
   ```

   Для текущего набора ожидаются 9 PDF (64 страницы) и 5 878 статей JSONL.
   У JSON-статьи `page_number=1` — логическая секция, ссылка на источник — `source_url`.
   Если документы есть,
   но поиск пустой, проверьте другой запрос и отметки `needs_review` у страниц:
   страницы с низкой оценкой OCR исключаются из выдачи.

Выполняйте команды последовательно и переходите к следующей только после успешного
завершения предыдущей. Диагностика PostgreSQL: `docker compose logs db`.

## Где найти результат и теги

| Путь | Содержимое | Команда, которая его создаёт |
|---|---|---|
| `data/raw/` | Исходные CSV, PDF и JSONL из Git | Получение репозитория |
| `data/processed/csv/` | Очищенные таблицы, `rejected.jsonl`, `manifest.json` | `prepare-csv` |
| `data/processed/pdf/` | Текст страниц и чанки PDF **без тегов** | `extract-pdfs` |
| `data/processed/news/` | Новости и чанки JSONL **без тегов** | `prepare-news` |
| `data/processed/tagged/pdf/` | PDF-артефакты **с тегами** | `tag-chunks` |
| `data/processed/tagged/news/` | Новостные артефакты **с тегами** | `tag-chunks` |
| `data/processed/tagged/report.json` | Количество чанков, частоты тегов и результаты классификации | `tag-chunks` |
| `data/cache/` | Веса модели и кэш классификации | `tag-chunks` |
| Docker volume `insights-db` | PostgreSQL с загруженными таблицами и документами | `load-csv`, `load-pdfs`, `load-news` |

В JSON метки находятся в **`pages[].chunks[]`**, отдельно для каждого чанка:
`tags`, `factors`, `sector`, `countries`, `regions`, `tagging_version`.
Пять флагов в `factors`: `key_rate`, `fx`, `commodities`, `consumer_demand`,
`construction_market`. В JSON это 0/1, в БД — boolean.
`sector.value=null` означает, что отрасль не принята по порогам классификатора;
это не ошибка загрузки. Слова в заголовке не добавляют теги автоматически ко всем чанкам.

Для просмотра, например, откройте `data/processed/tagged/pdf/ALFA-consumption.pdf.json`.
У исходной и размеченной версии одинаковое имя, но разные папки.
Появление JSON на диске ещё не означает загрузку в PostgreSQL: обязательно выполните
`load-pdfs` и `load-news` после `tag-chunks`. Они по умолчанию читают папки `tagged/`.

Если нужны только файлы для своего обработчика, достаточно команд подготовки
и `tag-chunks`; загрузку документов можно пропустить. Используйте файлы из
`outputs` соответствующего `manifest.json`: в папке могут остаться артефакты
предыдущего запуска, которые больше не входят в текущий набор.

## Проверить полноту загрузки

Откройте SQL-консоль DBeaver или выполните:

```powershell
docker compose exec db psql -U insights -d insights
```

Внутри `psql` выполните SQL ниже; для выхода в PowerShell введите `\q`.

```sql
SELECT 'companies' AS dataset, count(*) FROM insights.companies
UNION ALL SELECT 'person_links', count(*) FROM insights.person_links
UNION ALL SELECT 'payments', count(*) FROM insights.payments
UNION ALL SELECT 'news_articles', count(*) FROM insights.news_articles;

SELECT d.source_type, count(DISTINCT d.document_id) AS documents,
       count(*) AS chunks,
       count(*) FILTER (WHERE c.tagging_version IS NOT NULL) AS tagged
FROM insights.documents d
JOIN insights.document_chunks c USING (document_id)
GROUP BY d.source_type;

SELECT count(*) AS retrieval_chunks FROM insights.rag_chunks;
```

Ориентир для предоставленного снимка и стандартных параметров:

| Данные | Количество |
|---|---:|
| Компании | 85 513 |
| Связи физических лиц с компаниями | 12 514 |
| Платежи | 1 353 026 |
| PDF / их чанки | 9 / 163 |
| JSONL-новости / их чанки | 5 878 / 11 156 |
| Статьи для ленты (`news_articles`) | 5 878 |
| Все чанки / размеченные чанки | 11 319 / 11 319 |
| Чанки в `rag_chunks` | 11 317 |

В проверенном прогоне два чанка со страниц 4 и 20 файла
`FI_Strategy_3Q26_22072026.pdf` исключены из `rag_chunks` из-за низкой оценки OCR.
Они остаются в `document_chunks`; представление фильтрует страницы с `needs_review`.
Результаты OCR могут различаться при смене версии Tesseract или параметров обработки,
а изменение исходников, размера чанка и модели также меняет итоговые числа.
Проверить исключённые страницы:

```sql
SELECT d.source_file, p.page_number, p.confidence
FROM insights.document_pages p
JOIN insights.documents d USING (document_id)
WHERE p.needs_review;
```

## Как работать с готовой БД

Из DBeaver, Python или другого клиента на компьютере:

| Параметр | Значение |
|---|---|
| Host | `localhost` |
| Port | `POSTGRES_PORT` из `.env` (по умолчанию `5432`) |
| Database | `insights` |
| User | `insights` |
| Password | `POSTGRES_PASSWORD` из `.env` |
| Schema | `insights` |

В другом контейнере той же сети Compose используйте `db:5432`.
`localhost` на компьютере коллеги указывает на его локальную БД, а не на вашу.

Для retrieval используйте `insights.rag_chunks`: там есть текст, `chunk_id`, источник,
номер страницы/секции, дата публикации, URL новости, теги и флаги. Полные страницы — `insights.document_pages`;
профили — `insights.companies`; платежи — `insights.payments`; связи физлиц —
`insights.person_links`. Примеры запросов: [sql/retrieval_examples.sql](sql/retrieval_examples.sql).
Сейчас реализован полнотекстовый поиск с фильтром по тегам. Эмбеддинги для векторного
поиска и индекс добавляются отдельно по соглашению с командой retrieval;
модель в `tag-chunks` используется только для выбора отрасли.

Пример SQL для коллеги, который пишет retriever:

```sql
SELECT chunk_id, document_id, title, text, source_type, source_file,
       source_url, page_number, published_at, tags, tag_metadata
FROM insights.rag_chunks
WHERE tags @> ARRAY['factor:commodities', 'country:КИТАЙ']::text[]
ORDER BY published_at DESC NULLS LAST, chunk_id
LIMIT 20;
```

`@>` требует все перечисленные теги, `&&` — хотя бы один. Сравнение учитывает
регистр: передавайте полные строки тегов компаний без изменения написания.
Для цитирования новости используйте `source_url`, для PDF — `source_file`
и `page_number`. В `tag_metadata` находятся сектор, оценки кандидатов и
распознанные страны, для которых нет канонического тега компаний.
`tagging_version IS NULL` отличает старый неразмеченный чанк от обработанного
чанка, у которого все флаги false и массив `tags` пуст.

PostgreSQL хранит данные в Docker volume `insights-db` (обычно с префиксом имени
проекта). Для остановки и следующего запуска:

```powershell
docker compose stop
docker compose up -d db
```

Эти команды сохраняют данные. `docker compose down` также сохраняет volume,
а **`docker compose down -v` удаляет хранилище БД**. При смене пароля в `.env`
пароль уже созданного пользователя PostgreSQL автоматически не меняется.

После обновления Python-кода выполните `docker compose build ingest`. После
изменения исходных CSV повторите `prepare-csv` и `load-csv`; после изменения PDF
или метаданных — `extract-pdfs`, `tag-chunks` и `load-pdfs`. Для JSONL повторите
`prepare-news`, `tag-chunks` и `load-news`. Импорт CSV заменяет текущий снимок
целиком; неизменившиеся подготовленные документы повторно не импортируются.

После изменения исходников снова выполните `tag-chunks` перед загрузкой документов.
Неизменённые тексты будут размечены из кэша.
Если изменился CSV-словарь стран/регионов или правила разметки, повторите
`tag-chunks`, затем **обе** команды `load-pdfs` и `load-news`.
Кэш переиспользуется при совпадении текста и версии разметки.
Загрузка документов накопительная: удаление новости из исходного JSONL
не удаляет её автоматически из БД. При прерывании импорта повторите команду:
уже загруженные неизменившиеся документы будут пропущены.

### Обновить уже заполненную БД

Старые PDF не требуют повторного OCR, если в `data/processed/pdf/` есть готовые JSON:

```powershell
docker compose build ingest
docker compose run --rm ingest init-db
docker compose run --rm ingest prepare-news
docker compose run --rm ingest tag-chunks
docker compose run --rm ingest load-pdfs
docker compose run --rm ingest load-news
docker compose run --rm ingest load-news --source data/raw/reports/news.jsonl
```

`init-db` добавляет новые поля и индексы без удаления существующих документов.
При обновлении только тегов идентификаторы чанков сохраняются.
Подробно о словаре, флагах и выборе отрасли: [tagging.md](tagging.md).

Проверка обработчика, настоящего OCR и БД (создаётся отдельная временная тестовая БД):

```powershell
docker compose run --rm -e RUN_DB_TESTS=1 --entrypoint python ingest -m unittest tests.test_ingestion tests.test_database tests.test_news_tagging tests.test_news_data tests.test_news_archive tests.test_news_database -v
```

Образ общий для всех сервисов бэкенда, поэтому в нём проходят и тесты API:
`docker compose run --rm --entrypoint python ingest -m unittest discover -s tests`.
Локально полный набор запускается через `uv run pytest`
(см. также [инструкцию retrieval](retrieval.md#проверка)).

Подробности очистки, OCR и схемы: [ingestion.md](ingestion.md).

## Если запуск не получился

| Симптом | Что проверить и сделать |
|---|---|
| Docker не подключается к Server | Запустить Docker Desktop, дождаться готовности и повторить `docker version` |
| `ports are not available` / ошибка bind | Задать свободный `POSTGRES_PORT` в `.env`, например 5433, повторить `docker compose up -d db` |
| Не задан `POSTGRES_PASSWORD` | Создать `.env` из `.env.example`, заполнить пароль в корне проекта |
| `password authentication failed` после смены `.env` | В существующем volume пароль остался прежним; вернуть рабочую настройку или отдельно изменить пароль пользователя БД |
| Неизвестна команда `prepare-news` или `tag-chunks` | Получить актуальную ветку и выполнить `docker compose build ingest` |
| Нет `companies.csv` при разметке | Сначала выполнить `prepare-csv`; географический словарь читается из очищенного CSV |
| Не скачивается модель | Проверить доступ к Hugging Face и сеть Docker, затем повторить `tag-chunks`; первая загрузка требует интернета |
| В JSON нет тегов | Открыть файл из `data/processed/tagged/`, проверить `pages[].chunks[]` |
| Артефакты есть, а документов/тегов в БД нет | Выполнить `init-db`, затем `load-pdfs` и `load-news`, проверить SQL-счётчики выше |
| `artifact checksum mismatch` | Не редактировать результат вручную; повторить подготовку соответствующего источника, разметку и загрузку |
| Баннеры: «нет подходящих новостей» | `SELECT count(*) FROM insights.news_articles` равен 0 — выполнить `load-news --source data/raw/reports/news.jsonl` |
| Оркестратор: «нет файла тегов» | Выполнить `docker compose run --rm --entrypoint python ingest -m scripts.build_tags --from-db` |
| Поиск возвращает пустой список | Проверить счётчики, выполнить запрос без `--tag`, затем проверить написание тегов и `needs_review` |

Для диагностики используйте `docker compose ps` и `docker compose logs --tail 100 db`.
Не удаляйте volume для обычного перезапуска или обновления схемы.

## Что должно попасть в PR

В Git включаются код `src/backend/`, тесты, `sql/`, Docker-конфигурация,
`pyproject.toml` и `uv.lock`, `.env.example`, документация и исходные файлы `data/raw/`
с `data/reports-metadata.json`. Перед PR проверьте `git status --short`: новые
исходные файлы должны быть добавлены в коммит вместе с кодом.

В `.gitignore` исключены `.env` и локальные варианты `.env.*` (кроме шаблона
`.env.example`), `.venv`, Python-кэши, `data/processed/`, `data/cache/`,
сгенерированные диаграммы и локальные резервные копии БД. Данные Docker volume
находятся вне рабочей папки и через Git не передаются.
