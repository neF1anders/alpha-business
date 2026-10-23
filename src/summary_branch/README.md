# summary_branch: сигнал → саммари → баннеры

Ветка берёт `SummaryRequest` от оркестратора и отдаёт либо готовые баннеры в хранилище (`mode=banner`), либо один `ChatAnswer` (`mode=chat`). Поиск, ранжирование, профили компаний и BFF снаружи.

## Как работает

```
SummaryRequest ─ кластер × сегмент ─► SegmentCache ─промах─► LlmSummarizer ─► EvidenceBinder ─► content_signals ─► кэш
                                          │ попадание                                                            │
                                          └──────────────────────────────┬───────────────────────────────────────┘
                                                      для каждой компании сегмента (без LLM)
                                    ConfidenceScorer ─► BannerComposer ─► Publisher.to_dto ─► BannerStore.put ─► push
```

- **Дорого и общее** считается один раз на пару «кластер × сегмент» и лежит в `SegmentCache`. LLM вызывается только при промахе.
- **Дёшево и личное** (релевантность, свежесть, строка «для вас», срок жизни) считается на каждую компанию без токенов. Строка «для вас» берётся из шаблона модели: в слот `{feature}` подставляется признак компании (`features`), при отсутствии подходящего или пустом шаблоне берётся запасной текст.
- **Новое событие в кластере:** `runtime.invalidate_cluster(...)` сбрасывает и кэш саммари, и баннеры; следующий прогон пересобирает текст. Без этого кэш (TTL 72 ч) отдавал бы старое саммари.
- **Решение по уверенности** (`scoring.py`): ≥ 0.75 баннер, 0.55–0.75 баннер с `caveat`, ниже только заголовок и ссылка (`raw_news`), нет источников — баннера нет. Число, которого нет в источнике, ограничивает оценку 0.5.
- **Чат** ничего не пишет в `BannerStore`; при низкой уверенности или сбое модели возвращает `raw_news=True` с главной новостью.
- **Режим бюджета запроса** (`req.budget`, задаёт оркестратор): `full` обычный вызов, `lite` короткий без повтора, `cached_only` вызов модели запрещён (баннер пропускается, чат отвечает новостью).
- **Параллельность:** кластеры одного запроса `handle_banner` суммаризируются параллельно в потоках (до `max_parallel_clusters`), публикация идёт последовательно.
- **Безопасность:** новости, материалы банка, вопрос и теги компании вставляются в промпт как данные в экранированных разделителях; в промпте запрещены инвестиционные рекомендации.

## Контракты

| Что | Где | Суть |
|---|---|---|
| Вход | `models.SummaryRequest` | `trace_id`, `mode`, `question` (только chat), `company`, `news[]` по убыванию `rerank_score`, `bank_materials[]` (с обязательным `url`), `budget` |
| Выход banner | `list[Banner]`; в хранилище лежит ещё `BannerDTO` | личная часть: `for_you`, `confidence`, `decision`, `expires_at` |
| Выход chat | `models.ChatAnswer` | `claims`, `sources`, `caveat`, `raw_news`, `follow_ups`, `confidence` |
| Вход бэка | `LLMClient.generate_json(prompt, schema, *, tier, trace_id) -> dict` | `tier` = режим запроса (`"banner"` / `"chat"`), по нему клиент выбирает модель; готовая реализация: `llm_gateway.OpenAICompatibleLLMClient` |
| Справочник | `CompanyDirectory` | `active_in_segment(segment_id)`, `get(company_id)` |
| Кэш | `SegmentCache` | `get(key)`, `put(item, ttl_s)`, `invalidate_cluster(cluster_id) -> int` |
| Хранилище | `BannerStore` | `put -> bool` (новый и не скрытый ли баннер: пушить можно только при `True`), `top_k`, `mark_seen`, `suppress`, `invalidate_cluster` |

## Внедрение в бэк после оркестратора

```python
from summary_branch import build_branch, BranchConfig
from summary_branch.llm_gateway import OpenAICompatibleLLMClient

runtime = build_branch(
    llm=OpenAICompatibleLLMClient.from_env(),   # ключ API_KEY из .env или окружения
    directory=company_directory, # реализация CompanyDirectory
    # store и cache не передаём: для MVP работают in-memory (позже Redis/Postgres через те же интерфейсы)
    push_handler=sse_push,       # Callable[[company_id, list[BannerDTO]], None]
    config=BranchConfig(),       # значения по умолчанию из SPEC
)

banners = runtime.branch.handle_banner(request)   # предрасчёт, после реранкера
answer = runtime.branch.handle_chat(request)      # ответ в реальном времени
signals = runtime.top_banners(company_id)         # GET /signals, не более top_k_serve
runtime.store.mark_seen(company_id, banner_id)    # обратная связь
runtime.store.suppress(company_id, banner_id, "не про меня")
runtime.invalidate_cluster(cluster_id, "новое событие")   # кэш саммари + баннеры кластера
```

Условия корректной работы:
- Время `published_at` с часовым поясом (UTC); новости отсортированы по убыванию `rerank_score`; `segment_id` приходит готовым.
- В режиме `banner` вопроса нет, в `chat` он обязателен; иначе `SummaryRequest` не создастся (`ValidationError`).
- Ветка синхронная (внутри `handle_banner` кластеры идут параллельно в потоках). В асинхронном бэке вызывать через `asyncio.to_thread`.
- In-memory хранилища (по умолчанию) живут в одном процессе, защищены блокировкой, отдают копии объектов и сами чистят просроченное. Перезапуск процесса стирает баннеры и кэш саммари, то есть следующий прогон снова вызывает LLM.
- `handle_banner` и `handle_chat` не бросают исключений из-за модели: сбой, невалидный JSON после повтора и `cached_only` без кэша логируются, кластер пропускается (баннер) или отдаётся новостью (чат).

## Логи

Логгеры `summary_branch.*`, без текстов новостей и персональных данных. В каждой строке `trace_id` и, где есть, ключ контента (`cluster/segment/prompt_version`). Пишутся: попадание и промах кэша, вызов модели (tier, попытка, латентность, число утверждений), невалидный ответ и сбой клиента, отброшенные утверждения, уверенность (оценка, решение, причины) по каждой компании, сохранение баннера (`new=True/False`), срочный пуш, пропуск кластера. Хэндлеры и уровни настраивает бэк.

## LLM-адаптер

`llm_gateway.py`: OpenAI-совместимый шлюз по HTTPS, только стандартная библиотека. Ключ `API_KEY` берётся из `.env` и окружения (окружение приоритетнее), см. `.env.example`.
- **Маршрут по режиму запроса.** `banner`: сначала самая дешёвая по входным токенам (`deepseek/deepseek-v4.1-flash`, запасные `google/gemma-4-26b-a4b-it`, `qwen/qwen3.6-35b-a3b`). `chat`: сначала самая быстрая (`qwen/qwen3.6-35b-a3b`, запасная `deepseek/deepseek-v4.1-flash`). Порядок выбран замером на шлюзе команды; при смене шлюза его надо повторить. Меняется через `LLM_MODELS_BANNER` и `LLM_MODELS_CHAT` (модели через запятую, первая основная).
- **Отказоустойчивость.** Повтор при 429/5xx/таймауте, запасной адрес шлюза, затем следующая модель маршрута (в том числе при 403/404 на модель). Неверный ключ (401) прерывает цепочку сразу. Если все модели недоступны, ветка пропускает кластер (баннер) или отвечает новостью (чат).
- **Разбор ответа.** Убирает `<think>`, markdown-ограждение и текст вокруг JSON; обрезанный по `max_tokens` или пустой ответ считается невалидным (саммаризатор делает один повтор).
- **Рассуждение моделей отключено** (`reasoning.enabled=false`): иначе они тратят весь `max_tokens` на скрытые мысли и возвращают пустой ответ.
- Счётчики `calls`, `prompt_tokens`, `completion_tokens`. Ключ не попадает в логи и `repr`.

## Проверка

```
uv run pytest                                    # тесты на заглушках, без сети
uv run python scripts/demo_summary_branch.py     # демо: два сегмента, три компании, повтор через кэш
uv run python scripts/demo_summary_branch.py --live   # то же на реальном шлюзе (реальные вызовы)
```

Заглушки `FakeLLMClient` и `StaticDirectory` в `fakes.py` подходят для тестов бэка. Отклонения от исходной спецификации: `CHANGES.md`. Подробности потока и метрик: `summary-branch-dataflow.md`.

## Что не сделано

- Персистентность (Redis, Postgres) — отложена, для MVP хватает in-memory.
- Пороги, веса и калибровка уверенности на реальных данных: стартовые значения из `scoring.py`.
- Правило нарезки `segment_id` и режим вызова оркестратора (по сегменту или по компании) согласуются с соседними ветками.
