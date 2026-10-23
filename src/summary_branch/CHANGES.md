# Отклонения от SPEC.md и открытые вопросы

`models.py`, `interfaces.py` и пороги в `scoring.py` не менялись.

## Отклонения и дополнения

1. **Раскладка.** Ветка лежит в `src/summary_branch/` (по просьбе владельца), в корне репозитория добавлены
   `pyproject.toml` (`pydantic`, dev: `pytest`) и `uv.lock`. Модули плоские, как требует SPEC; слои Clean Architecture
   соблюдены по зависимостям (`models` <- `scoring`/`binder`/... <- `branch`), сборка связей только в `bootstrap.py`.
2. **Новые файлы вне списка SPEC:** `errors.py` (`SummarizationFailed`, `BudgetExhausted`) и `bootstrap.py`
   (composition root `build_branch`).
3. **NLI.** SPEC 3.5 просит принимать NLI в `DefaultConfidenceScorer`, но NLI используется в `content_signals`
   (уровень сегмента), а не в `company_confidence`. NLI принимает `DefaultContentScorer` (в `confidence.py`),
   калибратор принимает `DefaultConfidenceScorer`.
4. **Приватные хелперы `scoring`.** `binder.py` импортирует `_NUM`, `_numbers`, `_stems`, чтобы нормализация чисел в
   цитатах совпадала с проверкой обоснованности. Стоит сделать их публичными.
5. **Tier для роутера.** `LlmSummarizer` передаёт `tier=req.budget.value` (`"full"`/`"lite"`); соответствие
   конкретным моделям задаёт роутер.
6. **`for_you` при низкой уверенности.** `DefaultBannerComposer` кладёт пустую строку в `Banner.for_you` при
   `SHOW_RAW_NEWS` (а не только в DTO), чтобы личное содержимое не лежало в хранилище.
7. **Промпт banner-режима не содержит данных компании**, иначе общий кэш сегмента нельзя было бы использовать.
   В chat-режиме контекст компании в промпт пока не добавлен.

## Допущения

- Материал банка без `url` попадает в источники с пустым `url=""` (`SourceRef.url` обязателен). Нужно решить с BFF.
- `suppress` хранится отдельно от баннеров и переживает `invalidate_cluster`: после новой публикации того же `banner_id`
  баннер остаётся скрытым.
- Лид кластера — новость с максимальным `rerank_score` (`SegmentBuilder` сам сортирует).
- Пустые саммари (нет fact/bank или `structure_ok=False`) в кэш не пишутся, повторный прогон снова вызовет LLM.

## Открытые вопросы

- **Повторный пуш.** При повторном прогоне срочные баннеры снова уходят в `Publisher.push`, так как `BannerStore` не
  сообщает, что баннер уже был. Нужен либо флаг «новый» в ответе `put`, либо учёт отправленных в публикаторе.
- **SQS specificity.** `content_signals` ставит `1.0`, если в шаблоне есть `{feature}`; метрика из data-flow (раздел 6)
  требует упоминания признака компании. Не менялось.
- В `summary-branch-dataflow.md` упомянута `scoring.score_confidence`, в коде это `company_confidence`.
