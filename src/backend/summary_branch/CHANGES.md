# Отклонения от SPEC.md

Пороги и веса в `scoring.py` не менялись. Контракты изменены по прямому разрешению владельца:

- `BannerStore.put` возвращает `bool` (`True`, если `banner_id` новый для компании). Пуш срочного баннера только при `True`.
- `BankMaterialRef.url` обязателен (`str`).
- `SummaryCache.invalidate_cluster(cluster_id) -> int`; `BranchRuntime.invalidate_cluster` сбрасывает кэш и баннеры.
- `scoring.render_for_you`: пустой шаблон «для вас» даёт `fallback`, а не пустую строку.

Вход через `handle` и проверка внутри ветки (по указанию владельца):

- Добавлены `SummaryBranch.handle(req) -> list[BannerDTO] | ChatAnswer` и `BannerStore.get(company_id, banner_id) -> Optional[BannerDTO]`. Оркестратор не оценивает результат и не зацикливает ветку.
- Добавлен `generator.py` (`ValidatedGenerator`): проверка и повтор генерации. `ClusterSummaryBuilder` и `DefaultSummaryBranch` теперь принимают генератор вместо summarizer, binder и content_scorer.
- `BranchConfig`: `max_generation_attempts=2`, `min_grounding=0.6`.
- `handle_banner` и `handle_chat` сохранены. Повтор генерации действует и на них.
- Баннер с `raw_news`, сохранённый после исчерпанных попыток, считается готовым и отдаётся без новой генерации до истечения срока.
- Срочный пуш остался в ветке (он для других открытых сессий сегмента, а не для компании запроса).

Пороги и TTL по результатам синтетических прогонов (`eval/results/REPORT.md`):

- `scoring.py`: `STRICT_NUMERIC=True` (любое число проверяемого утверждения должно быть в источнике; прежнее правило `NUMERIC_MISS_GROUNDING` осталось под флагом, чтобы свип воспроизводил «было»), `MIN_CLAIM_LEX=0.5`, нормализация «полпроцента/полтора/полторы». В `ContentSignals` добавлено поле `unsupported_claim` (по умолчанию `False`); `ValidatedGenerator.is_valid` и потолок оценки 0.5 учитывают его.
- `config.py`: `banner_ttl_h` rate 168, fx 36, labor 120, demand 120 (остальное 36); `cache_ttl_s` 168 ч.
- Чат: маршрут `deepseek/deepseek-v4.1-flash`, запасная `qwen/qwen3.6-35b-a3b` (`llm_gateway.DEFAULT_ROUTES`, `.env.example`); промпт чата просит укороченный вывод, `follow_ups` в чате теперь пустой.
- Промпт: пересказ теми же лексемами, выводы только обоснованные. NLI не применяем.
- Пороги `HIGH`, `MEDIUM`, `min_grounding`, `HALF_LIFE_H` не менялись.

Персонализация по профилю (данные организаторов; расчёты в `eval/results/REPORT.md`, `eval/results/PERSONALIZATION.md`):

- Контракт расширен обратно совместимо: `CompanyProfile`, `PaymentProfile`, `UserContext`, `CompanyContext.profile`, `SummaryRequest.user` и свойство `role`, `ConfidenceComponents.personal_fit`. Без профиля поведение прежнее.
- Новый модуль `personalization.py`: чувствительность к темам, `segment_signature`, теги и признаки из профиля, `enrich`.
- `scoring.company_confidence`: релевантность по профилю (0.35 реранкер, 0.45 чувствительность, 0.10 отрасль/косвенная связь, 0.10 регион), порог `EXPOSURE_FLOOR=0.5` (ниже: не выше `RELEVANCE_CAP`), параметры `severity` и `requested_by_user`, потолок для «возможности» при негативном флаге. Выбор порога: шум 23% при 2 упущенных из 97 против 42% и 0 упущенных при 0.25.
- `render_for_you`: не клиенту строка не говорит о продуктах банка.
- Промпт: добавлена роль пользователя; приватность проверяется тестом.
- Данные организаторов в репозитории нет; скрипты читают их из внешнего каталога (`scripts/eval/run_personalization.py --data-dir`).
- Пробелы: сопоставление валюты новости и стран торговли, смысл `CLOSE_CLIENT`, отраслевые теги новостей, калибровка по реальной обратной связи.

Локальное хранилище:

- Добавлен `SqliteBannerStore` (`sqlite_store.py`) и `BranchConfig.banner_store_path`; без него баннеры в ОЗУ, как раньше. Внешней БД нет.
- `InMemoryBannerStore`: DTO хранились по одному `banner_id` на все компании, одинаковый id у двух компаний затирал выдачу; теперь ключ `(company_id, banner_id)`.
- Чат: `BranchConfig.chat_generation_attempts=1` (повтор удваивает задержку при SLO 4 с); для баннеров остаётся `max_generation_attempts=2`.
- Промпт чата: не более трёх утверждений до 25 слов, последнее (`model`) обязано называть признак компании из `features`; follow_ups до двух.

Остальное:

1. Пакет лежит в `src/summary_branch/`; `pyproject.toml` в корне (`pydantic`, dev: `pytest`).
2. Добавлены `errors.py` и `bootstrap.py` (composition root).
3. NLI принимает `DefaultContentScorer`, калибратор принимает `DefaultConfidenceScorer`: NLI нужен на уровне сегмента.
4. `binder.py` импортирует приватные `_NUM`, `_numbers`, `_stems` из `scoring.py`, чтобы нормализация чисел совпадала с проверкой обоснованности. Их стоит сделать публичными.
5. `tier` для LLM-клиента равен режиму запроса (`"banner"` / `"chat"`): по нему выбирается модель. Режим бюджета `lite` влияет только на промпт и отсутствие повтора.
6. При `SHOW_RAW_NEWS` композер пишет в `Banner.for_you` пустую строку.
7. `suppress` хранится отдельно от баннеров и переживает `invalidate_cluster`.
8. Пустые саммари (нет `fact`/`bank` или `structure_ok=False`) в кэш не пишутся.
9. `put` возвращает `False` и для баннера, скрытого пользователем (иначе после `invalidate_cluster` скрытый баннер пушился бы снова); `invalidate_cluster` сбрасывает признак «просмотрен».
10. Любой сбой LLM-клиента, кроме невалидного JSON, превращается в `SummarizationFailed` без повтора: ретраи и таймауты остаются за роутером.
