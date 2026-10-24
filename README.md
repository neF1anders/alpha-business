# Альфа Инсайты: данные для RAG и архитектура

Репозиторий содержит исходные обезличенные CSV, аналитические PDF Альфа-Банка,
скрипты очистки/OCR и загрузки в PostgreSQL, а также описание архитектуры C4.
Каждый участник собирает свою локальную БД из исходных файлов. Дамп не требуется.

## Собрать БД с нуля после получения PR

Нужны Git и запущенный Docker Desktop в режиме Linux containers (на Linux — Docker
Engine с Compose). Python и Tesseract на компьютер устанавливать не нужно: они
включены в образ обработки. Для первой сборки нужен интернет для скачивания образов
и зависимостей. Команды ниже рассчитаны на PowerShell и выполняются из корня клона.

1. Получите ветку PR или обновите репозиторий после слияния. В клоне должны быть:

   ```text
   data/raw/users/       3 CSV
   data/raw/reports/     9 PDF
   data/reports-metadata.json
   ```

   Исходные данные и метаданные должны войти в PR. Папка `data/processed/` не входит:
   она будет создана автоматически. Если исходных файлов нет, получите их из ветки PR
   до запуска скриптов.

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

4. Запустите PostgreSQL, соберите обработчик и создайте таблицы:

   ```powershell
   docker compose up -d db
   docker compose ps
   docker compose build ingest
   docker compose run --rm ingest init-db
   ```

5. Подготовьте и загрузите CSV:

   ```powershell
   docker compose run --rm ingest prepare-csv
   Get-Content -Encoding utf8 data/processed/csv/manifest.json
   docker compose run --rm ingest load-csv
   ```

   Исключаются конфликтные профили и связи с ними, переводы самим себе и дубли.
   Все исключения сохраняются в `data/processed/csv/rejected.jsonl`. Контрагенты
   без карточки сохраняются как сущности без профиля.

6. Распознайте PDF и загрузите документы в БД:

   ```powershell
   docker compose run --rm ingest extract-pdfs --metadata data/reports-metadata.json
   Get-Content -Encoding utf8 data/processed/pdf/report.json
   docker compose run --rm ingest load-pdfs
   ```

   OCR 64 страниц может занять несколько минут. JSON в `data/processed/pdf/` —
   промежуточный результат; запись в PostgreSQL выполняет именно `load-pdfs`.
   При прерывании распознавания повторите `extract-pdfs`: готовые страницы берутся
   из кэша. Проверьте выборочно текст и числа по оригинальным PDF.

7. Проверьте, что документы загружены и доступны для поиска:

   ```powershell
   docker compose exec db psql -U insights -d insights -c "SELECT source_file, page_count FROM insights.documents ORDER BY source_file;"
   docker compose run --rm ingest search "ключевая ставка"
   ```

   Для текущего набора ожидаются 9 документов и 64 страницы. Если документы есть,
   но поиск пустой, проверьте другой запрос и отметки `needs_review` у страниц:
   страницы с низкой оценкой OCR исключаются из выдачи.

Выполняйте команды последовательно и переходите к следующей только после успешного
завершения предыдущей. Диагностика PostgreSQL: `docker compose logs db`.

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

Для retrieval используйте `insights.rag_chunks`: там есть текст, `chunk_id`, имя
PDF, номер страницы и дата публикации. Полные страницы — `insights.document_pages`;
профили — `insights.companies`; платежи — `insights.payments`; связи физлиц —
`insights.person_links`. Примеры запросов: [sql/retrieval_examples.sql](sql/retrieval_examples.sql).
Сейчас реализован полнотекстовый поиск; модель эмбеддингов и векторный индекс
добавляются отдельно по соглашению с командой retrieval.

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
или метаданных — `extract-pdfs` и `load-pdfs`. Импорт CSV заменяет текущий снимок
целиком; неизменившиеся подготовленные документы повторно не импортируются.

Проверка обработчика, настоящего OCR и БД (создаётся отдельная временная тестовая БД):

```powershell
docker compose run --rm -e RUN_DB_TESTS=1 --entrypoint python ingest -m unittest discover -s tests -v
```

Подробности очистки, OCR и схемы: [docs/ingestion.md](docs/ingestion.md).

## Что должно попасть в PR

В Git включаются код `ingestion/`, тесты, `sql/`, Docker-конфигурация,
`requirements.txt`, `.env.example`, документация и исходные файлы `data/raw/`
с `data/reports-metadata.json`. Перед PR проверьте `git status --short`: новые
исходные файлы должны быть добавлены в коммит вместе с кодом.

В `.gitignore` исключены `.env` и локальные варианты `.env.*` (кроме шаблона
`.env.example`), `.venv`, Python-кэши, `data/processed/`, `data/cache/`,
сгенерированные диаграммы и локальные резервные копии БД. Данные Docker volume
находятся вне рабочей папки и через Git не передаются.

## Архитектура C4

`docs/workspace.dsl` — упрощённая модель с видами `01-context`, `02-containers`
и `03-pipeline`. `docs/workspace-full.dsl` — полная модель с семью видами ниже.
Эти файлы независимы; скрипт генерации по умолчанию использует упрощённую модель.
## Виды
| Ключ | Что показывает |
|---|---|
| `01-context` | L1: ЛПР, команда банка, внешние источники, LLM-шлюз, CRM |
| `02-containers` | L2: F1 оболочка, F2 ассистент, F3 консоль, BFF, Ingestion Worker, Insight Service, хранилище, очередь |
| `03-components-ingestion` | L3: хуки → коннекторы → санитайзер → дедуп → классификатор → эмбеддер → индексатор → база; парсер материалов банка |
| `04-components-insight-service` | L3: конструктор запроса → retriever → реранкер → оркестратор → саммаризатор → сборщик claims → LLM-judge (уверенность) → API |
| `05-flow-new-news` | Сценарий: пришла новость |
| `06-flow-banner` | Сценарий: баннеры по тегам компании |
| `07-flow-chat` | Сценарий: вопрос в чате или раскрытие баннера |
## Как закомпилировать
DSL нельзя «собрать» обычным компилятором: его читает `structurizr-cli`, проверяет и экспортирует виды в другие форматы (Mermaid, PlantUML, DOT, JSON). Файл лежит в `docs/workspace.dsl`, команды запускаются из корня проекта.

### Один раз: установка
Нужна Java 17+ (`java -version`). Дальше:
```bash
mkdir -p ~/.local/share/structurizr-cli ~/.local/bin
curl -sL -o /tmp/structurizr-cli.zip https://github.com/structurizr/cli/releases/latest/download/structurizr-cli.zip
unzip -q -o /tmp/structurizr-cli.zip -d ~/.local/share/structurizr-cli
ln -sf ~/.local/share/structurizr-cli/structurizr.sh ~/.local/bin/structurizr-cli
structurizr-cli version   # проверка; ~/.local/bin должен быть в PATH
```

### Каждый раз: DSL → Mermaid
```bash
scripts/build-diagrams.sh
```
Скрипт проверяет `docs/workspace.dsl` и кладёт по файлу на каждый вид в `docs/diagrams/structurizr-<ключ-вида>.mmd` (например, `structurizr-02-containers.mmd`). Папка `docs/diagrams/` генерируется: правьте DSL, а не `.mmd`. Если после запуска нет ошибок, DSL корректен.

То же вручную:
```bash
structurizr-cli validate -workspace docs/workspace.dsl                                   # только проверка
structurizr-cli export -workspace docs/workspace.dsl -format mermaid -output docs/diagrams   # plantuml, dot, json — другие форматы
```

### Просмотр в VS Code
- `.mmd`: расширение `vstirbu.vscode-mermaid-preview`, команда «Mermaid Preview: Preview Diagram» (открыть сгенерированный файл из `docs/diagrams/`).
- `.dsl`: расширение `ciarant.vscode-structurizr` даёт подсветку и подсказки. Картинку DSL VS Code сам не рисует, поэтому открывайте сгенерированные `.mmd` (после правки DSL перезапустите скрипт).
- Интерактивный просмотр всех видов с автолайаутом в браузере (нужен docker), http://localhost:8080:
  ```bash
  docker run -it --rm -p 8080:8080 -v $PWD/docs:/usr/local/structurizr structurizr/lite
  ```
  Lite ищет файл `workspace.dsl` в смонтированной папке, поэтому монтируется `docs/`.

## Допущения
- «Jev» прочитан как **LLM-judge** (компонент `confidenceJudge`). Высокая уверенность → резюме; низкая → оркестратор показывает саму новость или повторяет поиск.
- Роутер LLM и бюджет вынесены в отдельный компонент, чтобы показать лимит 500 ₽ в день.
- Компонент «Доступ и политики» — место для RBAC и allow-list инструментов (требование про безопасность ИИ).
- Выдача строится двумя путями: баннеры — запрос по тегам компании и роли; чат — запрос из контекста вопроса и экрана. Дальше один и тот же retriever → ranker → оркестратор.
