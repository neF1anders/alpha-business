# Контракт фронт ↔ бэк

Реализация: `src/backend/orchestrator/api/client.py`. Клиент фронта: `src/frontend/src/api/http-client.js`.
Авторизации нет. Компания задаётся в пути. Фронт по умолчанию шлёт `demo-company`; бэк подставляет вместо него `DEMO_COMPANY_ID`.

Префикс всех путей: `/api/companies/{company_id}`.

Необязательный query-параметр `role` принимают все клиентские эндпоинты (`signals`, `signals/inline`, `signals/stream`, `signals/{id}/seen|suppress|dismiss`, `chat`): `owner | ceo | cfo | treasurer | chief_accountant | procurement | sales`. Роль меняет ранжирование баннеров и персональный текст `for_you`. Баннеры, обратная связь и поток кэшируются по паре «компания × роль»: у каждой роли своя выдача и свои скрытые баннеры. Без `role` работает прежняя выдача для компании. Фронт добавляет `role` ко всем запросам, включая URL `EventSource`, когда роль известна (демо-вход, см. `src/frontend/README.md`).

| Метод и путь | Назначение | Ответ |
|---|---|---|
| `GET /signals` | старт сессии: баннеры по свежим новостям и сохранённые баннеры прошлых сессий | `{counts, items: BannerDTO[]}` |
| `GET /signals/stream` | SSE: готовые баннеры и снятие устаревших | события `banners`, `invalidate` |
| `GET /signals/inline?page=payments\|counterparties\|new_payment` | плашка на странице действия | `{item: BannerDTO \| null}` |
| `POST /signals/{banner_id}/seen` | баннер просмотрен | `204` |
| `POST /signals/{banner_id}/suppress` | «Не про меня», тело `{reason}` | `204` |
| `POST /signals/{banner_id}/dismiss` | «Скрыть баннер» без негативной обратной связи | `204` |
| `POST /chat` | вопрос, тело `{question, banner_id?}` | `ChatAnswer` |

## BannerDTO

```json
{
  "banner_id": "3f9c…",
  "cluster_id": "headline:ab12…",
  "severity": "attention | watch | opportunity",
  "label": "Внимание · ставка",
  "headline": "до 60 символов",
  "for_you": "персональная строка по путям влияния: отрасль, продукты, ВЭД, поставщики, покупатели",
  "inline_text": "текст плашки (сейчас равен for_you)",
  "caveat": false,
  "raw_news": false,
  "placements": ["payments", "new_payment"],
  "sources": [{"kind": "primary | bank | media", "title": "…", "url": "https://…", "ref_id": "news_id | doc_id"}],
  "expires_at": "2026-10-26T12:00:00Z"
}
```

- `raw_news=true`: низкая уверенность или новость не успела к сроку. Тогда `headline` — заголовок новости, а `for_you` пустой.
- Баннер «только новость», не успевший к сроку, приходит с `banner_id = raw-<cluster_id>`. Когда ветка доводит его, по SSE приходит `invalidate ["raw-<cluster_id>"]`, затем `banners [готовый]`.
- `placements` — страницы действий, где путь влияния уместен как плашка: поставщики → `payments` и `new_payment`, покупатели → `payments` и `counterparties`.
- Ссылки на материалы банка абсолютные: бэк дописывает свой origin к `/v1/materials/…`.

`counts` считается по всем актуальным баннерам выдачи: `{attention, watch, opportunity, hidden}`. В `hidden` попадают только баннеры, убранные кнопкой «Скрыть». «Не про меня» туда не входит.

## GET /signals/stream

Формат кадров:

```
event: banners
data: [BannerDTO, …]

event: invalidate
data: ["raw-<cluster_id>", …]
```

Каждые 15 с идёт комментарий `: ping`. После переподключения фронт перечитывает `GET /signals`. Если SSE трижды подряд обрывается, фронт переходит на опрос раз в 45 с.

## ChatAnswer

```json
{
  "answer_id": "…",
  "claims": [{"type": "fact | bank | model | hypothesis", "text": "…",
              "evidence": [{"ref_id": "news_id | doc_id", "quote": "≤ 200 символов"}], "grounding": 0.83}],
  "sources": [SourceRef],
  "caveat": false,
  "raw_news": false,
  "follow_ups": [],
  "confidence": 0.81
}
```

`evidence.ref_id` совпадает с `sources[].ref_id`: так фронт связывает утверждение с источником.

Если в запросе есть `banner_id` («Подробнее» из баннера), поиск идёт по заголовку новости этого баннера.

## Ошибки

`4xx/5xx` возвращаются телом `{"error": "code", "message": "…"}`. Коды:

| Код | Статус | Когда |
|---|---|---|
| `company_not_found` | 404 | компании нет |
| `invalid_request` | 422 | тело или параметры не прошли проверку |
| `unavailable` | 503 | поиск недоступен (только чат) |

Сбой LLM ошибкой не считается: приходит `raw_news=true`.

## Внутренний API ядра

`/v1/banners`, `/v1/chat`, `/v1/ws`, `/v1/companies/{id}/profile`, `/v1/materials/{id}` — это вход для отладки и для будущего BFF, см. [orchestrator.md](orchestrator.md). Со `/v1/ws` удобно смотреть шаги трассы по ходу запроса.
