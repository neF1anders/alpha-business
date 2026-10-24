/*
 * Альфа Инсайты — C4 (Structurizr DSL)
 * Уровни: Context -> Container -> Component (Ingestion, Insight Service) + 3 динамических сценария.
 *
 * Оркестратор только просит у ветки саммари баннеры или ответ чата и отдаёт их клиенту. Проверку качества
 * (компонент confidenceValidator, без LLM) и повтор генерации делает сама ветка; готовый баннер отдаётся
 * сразу из локального хранилища (bannerStore).
 *
 * Компиляция:
 *   structurizr-cli validate -workspace workspace.dsl
 *   structurizr-cli export   -workspace workspace.dsl -format mermaid   (или plantuml / dot / json)
 *   structurizr-lite (docker run -it --rm -p 8080:8080 -v $PWD:/usr/local/structurizr structurizr/lite)
 */
workspace "Альфа Инсайты" "Ассистент-суммаризатор новостей для ЛПР крупного бизнеса внутри Альфа-Бизнеса" {

    model {
        # ---------- Люди ----------
        lpr = person "ЛПР" "CEO, CFO, казначей, главбух, руководитель закупок/логистики; пользователь интернет-банка" "User"
        expert = person "Команда и эксперт банка" "Смотрит метрики, трассу, разбирает обратную связь" "Staff"

        # ---------- Внешние системы ----------
        news = softwareSystem "Источники новостей" "RSS, открытые API, страницы; только разрешённые правилами ресурсов (ЦБ РФ, госсайты и т.п.)" "External"
        bankDocs = softwareSystem "Аналитика Альфа-Банка" "9 материалов Центра макроисследований (PDF)" "External"
        bankData = softwareSystem "Данные клиентов банка" "Компании, связи с физлицами, платежи (синтетика, CSV)" "External"
        llmGateway = softwareSystem "LLM-шлюз банка" "Только разрешённые модели: Gemma 4 26B A4B, GLM-5.3-Flash, Qwen3.6 35B A3B, DeepSeek-V4.1-Flash, Qwen3-VL-30B-A3B, Kimi-K2.6; лимит токенов" "External"
        crm = softwareSystem "CRM и чат с менеджером" "Заявка, звонок, сообщение менеджеру (для прототипа — заглушка)" "External"

        # ---------- Наша система ----------
        insights = softwareSystem "Система инсайтов" "Отбирает значимые события, объясняет влияние на конкретную компанию, показывает источники" {

            # --- Frontend ---
            shell = container "F1 Оболочка Альфа-Бизнеса" "Имитация интернет-банка: главная, платежи, контекстные плашки" "SPA (React)" "WebApp"
            assistantUi = container "F2 Ассистент (правая панель)" "Баннеры-сигналы, раскрытый чат, голосовой/текстовый ввод, источники, обратная связь" "SPA-модуль" "WebApp"
            consoleUi = container "F3 Консоль" "Метрики качества, трасса агента, очередь обратной связи, управление источниками" "SPA-маршрут" "WebApp"

            bff = container "BFF" "Демо-авторизация, выбор компании и роли, агрегация, SSE-стрим ответов, кэш ответов UI" "Node.js / FastAPI" "Service"

            # --- Данные ---
            db = container "Хранилище" "Новости, кластеры событий, теги, векторы, claims, инсайты, трасса, обратная связь, профили и граф связей" "PostgreSQL + pgvector" "Database"
            queue = container "Очередь событий" "Событие «новая новость», задачи обработки, ночной предрасчёт" "Redis / RabbitMQ" "Queue"

            # --- Ingestion ---
            ingestion = container "Ingestion Worker" "Приём, очистка, разметка и индексация новостей и материалов банка" "Python" "Service" {
                hook = component "Хуки и планировщик" "Webhook новой новости, расписание опроса источников, повтор при сбое, резервные источники" "FastAPI + APScheduler"
                connectors = component "Коннекторы источников" "RSS, API, HTML-парсеры; соблюдение правил ресурсов и лимитов" "feedparser / httpx"
                sanitizer = component "Санитайзер" "Очистка разметки, защита от инъекций: текст новости — данные, не инструкции" "Python"
                normalizer = component "Нормализатор и дедуп" "Язык, дата, источник; кластеры событий (MinHash + эмбеддинги); фиксация расхождений между источниками" "Python"
                classifier = component "Классификатор" "Сфера влияния, макрофактор (ставка, курс, труд, спрос…), география, отрасли (ОКВЭД); классика + Gemma для сложных" "scikit-learn + Gemma 4"
                embedder = component "Эмбеддер" "Векторы новостей и фрагментов материалов банка" "локальный OSS-эмбеддер"
                bankParser = component "Парсер материалов банка" "PDF → тезисы и claims с номером страницы; графики и таблицы читает Qwen3-VL; один раз офлайн" "PyMuPDF + Qwen3-VL"
                indexer = component "Индексатор" "Запись новостей, тегов, векторов и claims в хранилище" "SQLAlchemy"
            }

            # --- Insight Service ---
            insightSvc = container "Insight Service" "Подбор и ранжирование, оркестрация, саммари с LLM, оценка уверенности, выдача сигналов и ответов чата" "Python" "Service" {
                api = component "Insight API" "REST + SSE: /signals, /insights, /ask, /context, /feedback, /history" "FastAPI"
                guard = component "Доступ и политики" "Роль, компания, RBAC, allow-list инструментов (только чтение), редакция ПДн" "Python"
                profile = component "Профиль компании" "Паспорт: отрасль, масштаб, регион, продукты, группа (Group ID), роль; правки пользователя; выручка 1000 = NULL" "Python"
                graph = component "Граф связей" "Платежи и группа без переводов себе; контрагент без профиля — лист; связи только как гипотезы" "networkx"
                signalBuilder = component "Сборщик сигналов" "Баннеры для панели: статус, строка «для вас», порог влияния и уверенности" "Python"
                queryBuilder = component "Конструктор запроса" "Строит запрос из тегов компании и роли (баннеры) либо из контекста чата и экрана (вопрос пользователя)" "Python"
                retriever = component "Retriever" "Гибридный поиск: фильтр по тегам + BM25 + векторы; опционально веб-поиск по разрешённым источникам" "pgvector + tsvector"
                ranker = component "Реранкер" "Cross-encoder + классическое ранжирование (LTR) под профиль: пул top-K кандидатов" "cross-encoder + LightGBM"
                orchestrator = component "Оркестратор" "План → инструменты → самопроверка; повторяет поиск при низкой уверенности; решает: резюме или сама новость" "LangGraph"
                cache = component "Сегментный кэш" "Ключ (кластер новости, сегмент компании); тонкая персонализация поверх" "Redis"
                summarizer = component "Саммаризатор и интерпретатор" "Что произошло, почему важно этой компании, последствия, что сделать; опора на материалы банка" "LLM"
                claimBuilder = component "Сборщик claims и ссылок" "Разбивает на утверждения: факт / позиция банка / вывод модели / гипотеза; привязывает источники и первоисточник" "Python + JSON schema"
                confidenceValidator = component "Валидатор уверенности" "Числа из источника, обоснованность, качество источника, свежесть; без LLM; выдаёт оценку и причины" "Python"
                bannerStore = component "Хранилище баннеров" "Локальное: баннеры по компаниям; готовый валидный баннер отдаётся без генерации" "in-memory"
                llmRouter = component "LLM-роутер и бюджет" "Каскад моделей, лимит токенов и ₽, деградация при исчерпании" "Python"
                trace = component "Трасса и журнал" "Шаги агента, вызовы инструментов, промежуточные результаты, trace-id" "Python"
                feedback = component "Обратная связь и метрики" "Полезно / не про меня / неточно; показы; метрики качества для консоли" "Python"
            }
        }

        # ---------- Связи: контекст ----------
        lpr -> shell "Работает в интернет-банке" "HTTPS"
        lpr -> assistantUi "Читает баннеры, задаёт вопросы текстом и голосом" "HTTPS"
        expert -> consoleUi "Контролирует качество" "HTTPS"
        insights -> news "Забирает новости" "RSS / API / HTTPS"
        insights -> bankDocs "Загружает материалы банка" "файлы"
        insights -> bankData "Читает профили и платежи" "CSV / SQL"
        insights -> llmGateway "Вызывает разрешённые LLM" "HTTPS"
        insights -> crm "Передаёт заявку / сообщение менеджеру" "API"

        # ---------- Связи: контейнеры ----------
        shell -> assistantUi "Встраивает панель и плашки" "компонент"
        shell -> bff "Данные главной и плашек" "JSON/HTTPS"
        assistantUi -> bff "Сигналы, вопросы, обратная связь" "JSON/SSE"
        consoleUi -> bff "Метрики и трасса" "JSON/HTTPS"
        bff -> insightSvc "Запросы с компанией и ролью" "REST/SSE"
        news -> ingestion "Новости" "RSS / API"
        bankDocs -> ingestion "PDF"
        queue -> ingestion "Задачи обработки"
        ingestion -> queue "Событие «новость обработана»"
        ingestion -> db "Пишет новости, теги, векторы, claims"
        ingestion -> llmGateway "Разметка и чтение графиков" "HTTPS"
        insightSvc -> db "Читает и пишет"
        insightSvc -> queue "Ночной предрасчёт, задачи"
        insightSvc -> llmGateway "Саммари" "HTTPS"
        insightSvc -> crm "Заявка, звонок, сообщение" "API"
        bankData -> insightSvc "Профили, связи, платежи"

        # ---------- Связи: Ingestion ----------
        hook -> connectors "Запускает сбор"
        connectors -> sanitizer "Сырой текст"
        sanitizer -> normalizer "Очищенный текст"
        normalizer -> classifier "Событие (кластер)"
        classifier -> embedder "Размеченное событие"
        embedder -> indexer "Векторы"
        bankParser -> embedder "Тезисы банка"
        bankParser -> indexer "Claims банка"
        indexer -> db "INSERT/UPSERT"
        connectors -> news "Забирает" "RSS / API"
        bankParser -> bankDocs "Читает PDF"
        classifier -> llmGateway "Сложные случаи"
        bankParser -> llmGateway "Графики и таблицы"
        hook -> queue "Публикует событие новой новости"

        # ---------- Связи: Insight Service ----------
        bff -> api "Запросы"
        api -> guard "Проверка роли, компании, инструментов"
        api -> signalBuilder "Баннеры для панели"
        api -> orchestrator "Вопрос пользователя (чат) и раскрытие баннера"
        api -> feedback "Реакции пользователя"
        api -> trace "Трасса для консоли"
        signalBuilder -> queryBuilder "Запрос по тегам компании"
        orchestrator -> queryBuilder "Запрос из контекста чата и экрана"
        queryBuilder -> profile "Теги, отрасль, роль"
        queryBuilder -> retriever "Запрос + фильтры"
        retriever -> db "Теги + BM25 + векторы"
        retriever -> ranker "Кандидаты"
        ranker -> profile "Признаки компании"
        ranker -> graph "Экспозиция через контрагентов и группу"
        ranker -> orchestrator "Пул top-K"
        ranker -> signalBuilder "Пул top-K для баннеров"
        orchestrator -> cache "Проверка сегментного кэша"
        orchestrator -> summarizer "Пул + профиль + claims банка"
        summarizer -> llmRouter "Запрос к модели"
        llmRouter -> llmGateway "Выбранная модель"
        summarizer -> claimBuilder "Черновик саммари"
        claimBuilder -> confidenceValidator "Claims + источники"
        confidenceValidator -> summarizer "Невалидно: повтор генерации"
        confidenceValidator -> bannerStore "Валидно (или лучшее после повторов): сохранение"
        summarizer -> orchestrator "Баннеры или ответ"
        orchestrator -> bannerStore "Есть готовый баннер?"
        orchestrator -> api "Баннеры или ответ, claims, ссылки"
        orchestrator -> trace "Шаги плана"
        summarizer -> trace "Промпты и ответы"
        graph -> db "Читает связи"
        profile -> db "Профиль и правки"
        profile -> bankData "Первичная загрузка"
        trace -> db "Журнал"
        feedback -> db "Реакции и метрики"
        api -> crm "Передать менеджеру"
    }

    views {
        systemContext insights "01-context" "C4 L1: система, пользователи и внешние источники" {
            include *
            autolayout lr
        }

        container insights "02-containers" "C4 L2: контейнеры системы" {
            include *
            autolayout lr
        }

        component ingestion "03-components-ingestion" "C4 L3: как новости попадают в базу" {
            include *
            autolayout lr
        }

        component insightSvc "04-components-insight-service" "C4 L3: подбор, ранжирование, саммари и уверенность" {
            include *
            autolayout lr
        }

        dynamic ingestion "05-flow-new-news" "Сценарий 1: пришла новая новость" {
            news -> connectors "Новая публикация"
            connectors -> sanitizer "Сырой текст"
            sanitizer -> normalizer "Очищенный текст"
            normalizer -> classifier "Событие после дедупа"
            classifier -> embedder "Теги: сфера, фактор, география, отрасль"
            embedder -> indexer "Векторы"
            indexer -> db "Запись в базу"
            autolayout lr
        }

        dynamic insightSvc "06-flow-banner" "Сценарий 2: баннеры для панели (по тегам компании)" {
            bff -> api "GET /signals?company&role"
            api -> signalBuilder "Собрать баннеры"
            signalBuilder -> queryBuilder "Теги компании и роли"
            queryBuilder -> retriever "Запрос + фильтры"
            retriever -> ranker "Кандидаты"
            ranker -> signalBuilder "Пул top-K"
            signalBuilder -> api "3 баннера: статус, строка «для вас»"
            autolayout lr
        }

        dynamic insightSvc "07-flow-chat" "Сценарий 3: вопрос в чате или раскрытие баннера" {
            bff -> api "POST /ask (контекст экрана)"
            api -> orchestrator "Вопрос"
            orchestrator -> queryBuilder "Запрос из контекста"
            queryBuilder -> retriever "Запрос + фильтры"
            retriever -> ranker "Кандидаты"
            ranker -> orchestrator "Пул top-K"
            orchestrator -> summarizer "Пул + профиль + claims банка"
            summarizer -> claimBuilder "Черновик саммари"
            claimBuilder -> confidenceValidator "Claims + источники"
            confidenceValidator -> summarizer "Невалидно: повтор генерации"
            summarizer -> orchestrator "Ответ"
            orchestrator -> api "Саммари + ссылки или сама новость"
            autolayout lr
        }

        styles {
            element "Person" {
                shape person
                background #1F1F22
                color #ffffff
            }
            element "User" {
                background #EF3124
            }
            element "External" {
                background #8A8D94
                color #ffffff
            }
            element "WebApp" {
                shape webBrowser
                background #2457D6
                color #ffffff
            }
            element "Service" {
                background #4B27A8
                color #ffffff
            }
            element "Database" {
                shape cylinder
                background #12633A
                color #ffffff
            }
            element "Queue" {
                shape pipe
                background #7A4600
                color #ffffff
            }
            element "Component" {
                background #E4ECFD
                color #14213D
            }
            element "Software System" {
                background #4B27A8
                color #ffffff
            }
            relationship "Relationship" {
                thickness 2
                color #5B5D63
            }
        }
    }
}
