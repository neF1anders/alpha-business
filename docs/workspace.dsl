/*
 * Альфа Инсайты: C4, упрощённая версия (один конвейер на одной схеме).
 * Оркестратор только просит у ветки саммари баннеры или ответ чата (по типу запроса) и отдаёт их клиенту.
 * Проверку качества и повтор генерации делает сама ветка: готовый валидный баннер из локального хранилища
 * отдаётся сразу, иначе генерация -> валидация (числа, источники, обоснованность, без LLM) -> повтор или сохранение.
 *
 * Виды:
 *   01-context     кто и с чем взаимодействует
 *   02-containers  из каких частей состоит система
 *   03-pipeline    главная схема: новость -> база -> запрос -> поиск -> ранжирование -> ветка саммари (хранилище -> генерация -> валидация -> сохранение) -> оркестратор -> выдача
 *
 * Полная версия с 40 компонентами лежит в workspace-full.dsl.
 */
workspace "Альфа Инсайты" "Новости + аналитика банка -> персональные инсайты для ЛПР внутри Альфа-Бизнеса" {

    model {
        lpr = person "ЛПР" "CEO, CFO, казначей; работает в интернет-банке" "User"

        news = softwareSystem "Источники новостей" "RSS и открытые API (ЦБ РФ, госсайты и др.)" "External"
        bankDocs = softwareSystem "Аналитика банка" "9 материалов Центра макроисследований" "External"
        llm = softwareSystem "LLM-шлюз банка" "Только разрешённые модели, лимит токенов" "External"

        insights = softwareSystem "Система инсайтов" "Отбирает новости, объясняет влияние на компанию, показывает источники" {

            ui = container "Ассистент в интернет-банке" "Баннеры справа, чат с текстом и голосом, плашки на платежах" "SPA" "WebApp"
            db = container "База новостей" "Новости, теги, векторы, claims, профили, трасса" "PostgreSQL + pgvector" "Database"

            core = container "Ядро инсайтов" "Приём новостей, поиск, ранжирование, саммари, оценка уверенности" "Python" "Service" {
                sanitizer = component "Очистка, дедуп" "без инъекций" "Python"
                classifier = component "Классификатор" "теги и отрасль" "scikit-learn"
                embedder = component "Эмбеддер" "векторы, индекс" "OSS-эмбеддер"

                retriever = component "Retriever" "BM25 + векторы" "pgvector"
                ranker = component "Реранкер" "под профиль компании" "cross-encoder"
                profile = component "Профиль и граф" "признаки компании" "Python"
                orchestrator = component "Оркестратор" "просит у ветки баннер или ответ по типу запроса, отдаёт клиенту" "LangGraph"
                branch = component "Ветка саммари" "handle(): баннеры или ответ чата по типу запроса" "Python"
                bannerStore = component "Хранилище баннеров" "локальное; баннеры по компаниям" "in-memory"
                summarizer = component "Саммаризатор" "кэш сегмента, один вызов LLM на кластер" "LLM"
                claims = component "Claims и ссылки" "факт, банк, вывод, гипотеза; баннер на каждый кластер" "Python"
                validator = component "Валидатор уверенности" "числа, обоснованность, источники, свежесть, применимость к компании по профилю; без LLM" "Python" "Judge"
            }
        }

        # контекст и контейнеры
        lpr -> ui "Читает баннеры, задаёт вопросы"
        ui -> core "Запросы с компанией и ролью"
        core -> db "Читает и пишет"
        news -> core "Новости"
        bankDocs -> core "PDF банка"
        core -> llm "Саммари"

        # конвейер
        news -> sanitizer "Новости"
        sanitizer -> classifier "Чистый текст"
        classifier -> embedder "Теги"
        embedder -> db "Векторы и теги"

        ui -> orchestrator "Вопрос чата или запуск баннеров"
        orchestrator -> retriever "Запрос: теги компании или вопрос"
        db -> retriever "Кандидаты"
        retriever -> ranker "Кандидаты"
        profile -> ranker "Признаки компании"
        ranker -> orchestrator "Топ-K"
        orchestrator -> branch "Баннер или ответ (по типу запроса): новости + профиль компании и пользователя (ОКВЭД, продукты, ВЭД, платежи агрегатами)"
        branch -> bannerStore "Есть готовый баннер?"
        bannerStore -> branch "Да: отдаём сразу"
        branch -> summarizer "Нет: генерация"
        summarizer -> claims "Черновик"
        claims -> validator "Утверждения + источники"
        validator -> summarizer "Невалидно: повтор генерации"
        validator -> bannerStore "Валидно (или лучшее после повторов): сохранение"
        branch -> orchestrator "Баннеры или ответ"
        orchestrator -> ui "Отдаёт клиенту"
        summarizer -> llm "Запрос к модели"
    }

    views {
        systemContext insights "01-context" "Кто и с чем взаимодействует" {
            include *
            autolayout lr
        }

        container insights "02-containers" "Из каких частей состоит система" {
            include lpr ui core db news bankDocs llm
            autolayout lr
        }

        component core "03-pipeline" "Главная схема: от новости до выдачи" {
            include news db ui
            include sanitizer classifier embedder
            include retriever ranker profile orchestrator branch bannerStore summarizer claims validator
            autolayout tb 80 60
        }

        styles {
            element "Person" {
                shape person
                background #1F1F22
                color #ffffff
            }
            element "External" {
                background #8A8D94
                color #ffffff
            }
            element "WebApp" {
                shape webBrowser
                background #8A8D94
                color #ffffff
            }
            element "Database" {
                shape cylinder
                background #8A8D94
                color #ffffff
            }
            element "Service" {
                background #534AB7
                color #ffffff
            }
            element "Component" {
                background #EEEDFE
                color #26215C
            }
            element "Judge" {
                background #FAEEDA
                color #412402
            }
            element "Software System" {
                background #534AB7
                color #ffffff
            }
            relationship "Relationship" {
                thickness 2
                color #5B5D63
                fontSize 22
            }
        }
    }
}
