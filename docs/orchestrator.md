# Оркестратор

Вход и выход системы. Фронт шлёт запрос с компанией и типом (`banners` или `chat`), оркестратор собирает контекст и отдаёт результат.

```
запрос -> профиль -> retriever -> реранкер -> ветка саммари (кэш, генерация, валидация) -> ответ
```

SLO: баннеры 2 с, чат 6 с (настраивается `BANNER_SLO_S`, `CHAT_SLO_S`). Каждый внешний вызов ограничен временем. При сбое или таймауте ветки саммари отдаётся сама новость со ссылкой (`raw_news: true`), а не ошибка.

## Слои (зависимости направлены внутрь)

| Слой | Что внутри |
|---|---|
| `orchestrator/domain` | сущности, реранкер `TagReranker`, правила сегмента и признаков из тегов; только stdlib |
| `orchestrator/application` | порты (`ProfileRepository`, `NewsRetriever`, `Reranker`, `SummaryBranch`), сценарии `GetBanners` и `AnswerChat`, трасса и дедлайн |
| `orchestrator/infrastructure` | профили из `company_tags.csv`, шаблонная ветка саммари без LLM; синтетические новости оставлены только для тестов |
| `orchestrator/api` | FastAPI: HTTP и WebSocket, схемы pydantic |
| `orchestrator/main.py` | composition root: единственное место, где порты связываются с реализациями |

## API

Swagger: `GET /docs`.

| Метод | Назначение |
|---|---|
| `POST /v1/banners` | `{company_id, user_id?, role?}` -> баннеры. Триггер «сессия началась» |
| `POST /v1/chat` | то же + `question` -> ответ с `claims` и `sources` |
| `WS /v1/ws` | `{"type": "banners"\|"chat", company_id, question?}`; в ответ `progress` по шагам и итог (`banners` / `answer` / `error`) |
| `GET /v1/companies/{id}/profile` | сегмент, признаки и теги, которые использует система |
| `GET /health` | проверка живости |

`?debug=true` добавляет `steps`: шаги конвейера с длительностью и деталями (какие теги совпали, оценки реранкера). Это основа трассируемости.

## Запуск

```powershell
python -m scripts.build_tags --from-db  # нужна БД и настроенное окружение
# Также загрузите news_articles по docs/retrieval.md.
docker compose up -d --build orchestrator     # http://127.0.0.1:8000/docs
```

Без Docker: `pip install -r orchestrator/requirements.txt`, затем `python -m orchestrator`.
Подготовка данных, подключение PostgreSQL и опциональные локальные модели —
в [инструкции retrieval](retrieval.md).
Тесты: `python -m unittest discover -s tests -p "test_orchestrator*.py"` (нужны `fastapi`, `httpx`).

## Профиль и сегмент

`segment_id = сектор|размер`, например `trade|mid` (теги `sector:*` и `size:*`, см. `docs/tags.md`). Это ключ сегментного кэша в ветке саммари.
Профиль читается из `company_tags.csv` один раз при старте приложения (около 1.5 с), иначе первый запрос не уложился бы в SLO.

## Лексический реранкер

`score = 0.55·relevance + 0.20·retrieval + 0.15·свежесть + 0.10·источник`.
`relevance` считается как «шумное ИЛИ» по совпавшим тегам: factor 0.7, sector 0.5, okved 0.4, country 0.3, region 0.2.
Модификаторы по продуктам: новость про ключевую ставку важнее компаниям с кредитом, лизингом, факторингом, депозитом.
Остаётся лучшая новость на кластер. Веса стартовые, не калиброваны на данных.

## Что подключается дальше

- `NewsRetriever` уже подключён: `PostgresNewsRetriever`; при `NEWS_SEARCH_MODE=local_semantic` — `LocalFeedRetriever` и `LocalPairReranker`. Вопросы чата сохраняют лексический путь.
- `EventRetriever` подбирает банковские материалы отдельно для отобранных событий; `GET /v1/materials/{document_id}` выдаёт зарегистрированный PDF.
- `SummaryBranch`: сейчас `TemplateSummaryBranch` без LLM; адаптер к `summary_branch` (ветка `feat/orchestrator2banners`) должен вызвать `handle` в `asyncio.to_thread` и сопоставить типы 1 к 1 (`SummaryRequest`, `BannerDTO`, `ChatAnswer`).
- Авторизация и RBAC: сейчас `company_id` берётся из тела запроса без проверки прав. Для прототипа это допущение, в проде компания должна определяться по сессии.
