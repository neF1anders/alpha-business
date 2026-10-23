# summary_branch: сигнал → саммари → баннеры

Ветка берёт `SummaryRequest` от оркестратора и отдаёт либо готовые баннеры в хранилище (`mode=banner`), либо один `ChatAnswer` (`mode=chat`). Поиск, ранжирование, профили, LLM-шлюз и BFF снаружи.

## Как работает

```
SummaryRequest ─ кластер × сегмент ─► SegmentCache ─промах─► LlmSummarizer ─► EvidenceBinder ─► content_signals ─► кэш
                                          │ попадание                                                            │
                                          └──────────────────────────────┬───────────────────────────────────────┘
                                                      для каждой компании сегмента (без LLM)
                                    ConfidenceScorer ─► BannerComposer ─► Publisher.to_dto ─► BannerStore.put ─► push
```

- **Дорого и общее** считается один раз на пару «кластер × сегмент» и лежит в `SegmentCache`. LLM вызывается только при промахе.
- **Дёшево и личное** (релевантность, свежесть, строка «для вас», срок жизни) считается на каждую компанию без токенов.
- **Решение по уверенности** (`scoring.py`): ≥ 0.75 баннер, 0.55–0.75 баннер с `caveat`, ниже только заголовок и ссылка (`raw_news`), нет источников — баннера нет. Число, которого нет в источнике, ограничивает оценку 0.5.
- **Чат** ничего не пишет в `BannerStore`; при низкой уверенности или сбое модели возвращает `raw_news=True` с главной новостью.
- **Бюджет:** `full` обычный вызов, `lite` короткий без повтора, `cached_only` вызов запрещён (баннер пропускается, чат отвечает новостью).
- **Безопасность:** новости, материалы банка, вопрос и теги компании вставляются в промпт как данные в экранированных разделителях; в промпте запрещены инвестиционные рекомендации.

## Контракты

| Что | Где | Суть |
|---|---|---|
| Вход | `models.SummaryRequest` | `trace_id`, `mode`, `question` (только chat), `company`, `news[]` по убыванию `rerank_score`, `bank_materials[]` (с обязательным `url`), `budget` |
| Выход banner | `list[Banner]`; в хранилище лежит ещё `BannerDTO` | личная часть: `for_you`, `confidence`, `decision`, `expires_at` |
| Выход chat | `models.ChatAnswer` | `claims`, `sources`, `caveat`, `raw_news`, `follow_ups`, `confidence` |
| Вход бэка | `LLMClient.generate_json(prompt, schema, *, tier, trace_id) -> dict` | `tier` = `"full"` / `"lite"`; роутер сам выбирает модель |
| Справочник | `CompanyDirectory` | `active_in_segment(segment_id)`, `get(company_id)` |
| Кэш | `SegmentCache` | `get(key)`, `put(item, ttl_s)` |
| Хранилище | `BannerStore` | `put -> bool` (новый ли баннер), `top_k`, `mark_seen`, `suppress`, `invalidate_cluster` |

## Внедрение в бэк после оркестратора

```python
from summary_branch import build_branch, BranchConfig

runtime = build_branch(
    llm=gateway_client,          # реализация LLMClient поверх общего роутера
    directory=company_directory, # реализация CompanyDirectory
    store=redis_banner_store,    # реализация BannerStore (Redis + Postgres)
    cache=redis_segment_cache,   # реализация SegmentCache
    push_handler=sse_push,       # Callable[[company_id, list[BannerDTO]], None]
    config=BranchConfig(),       # значения по умолчанию из SPEC
)

banners = runtime.branch.handle_banner(request)   # предрасчёт, после реранкера
answer = runtime.branch.handle_chat(request)      # ответ в реальном времени
signals = runtime.top_banners(company_id)         # GET /signals, не более top_k_serve
runtime.store.mark_seen(company_id, banner_id)    # обратная связь
runtime.store.suppress(company_id, banner_id, "не про меня")
runtime.store.invalidate_cluster(cluster_id, "новое событие")
```

Условия корректной работы:
- Время `published_at` с часовым поясом (UTC); новости отсортированы по убыванию `rerank_score`; `segment_id` приходит готовым.
- В режиме `banner` вопроса нет, в `chat` он обязателен; иначе `SummaryRequest` не создастся (`ValidationError`).
- Ветка синхронная. В асинхронном бэке вызывать через `asyncio.to_thread`.
- `build_branch` без `store` и `cache` ставит in-memory реализации: они живут в одном процессе и не потокобезопасны. Для прода нужны Redis и Postgres; Redis-реализации в пакете нет.
- `handle_banner` и `handle_chat` не бросают исключений из-за модели: сбой, невалидный JSON после повтора и `cached_only` без кэша логируются, кластер пропускается (баннер) или отдаётся новостью (чат).

## Логи

Логгеры `summary_branch.*`, без текстов новостей и персональных данных. В каждой строке `trace_id` и, где есть, ключ контента (`cluster/segment/prompt_version`). Пишутся: попадание и промах кэша, вызов модели (tier, попытка, латентность, число утверждений), невалидный ответ и сбой клиента, отброшенные утверждения, уверенность (оценка, решение, причины) по каждой компании, сохранение баннера (`new=True/False`), срочный пуш, пропуск кластера. Хэндлеры и уровни настраивает бэк.

## Проверка

```
uv run pytest                                    # тесты на заглушках, без сети
uv run python scripts/demo_summary_branch.py     # демо: два сегмента, три компании, повтор через кэш
```

Заглушки `FakeLLMClient` и `StaticDirectory` в `fakes.py` подходят для тестов бэка. Отклонения от исходной спецификации: `CHANGES.md`. Подробности потока и метрик: `summary-branch-dataflow.md`.

## Что не сделано

- Реальные `LLMClient`, Redis и Postgres реализации.
- Пороги, веса и калибровка уверенности на реальных данных: стартовые значения из `scoring.py`.
- Правило нарезки `segment_id` и режим вызова оркестратора (по сегменту или по компании) согласуются с соседними ветками.
