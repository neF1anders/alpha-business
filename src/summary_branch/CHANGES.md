# Отклонения от SPEC.md

Пороги и веса в `scoring.py` не менялись. Контракты изменены по прямому разрешению владельца:

- `BannerStore.put` возвращает `bool` (`True`, если `banner_id` новый для компании). Пуш срочного баннера только при `True`.
- `BankMaterialRef.url` обязателен (`str`).
- `SegmentCache.invalidate_cluster(cluster_id) -> int`; `BranchRuntime.invalidate_cluster` сбрасывает кэш и баннеры.
- `scoring.render_for_you`: пустой шаблон «для вас» даёт `fallback`, а не пустую строку.

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
