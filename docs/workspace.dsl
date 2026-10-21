/*
 * Альфа Инсайты: C4, упрощённая версия (один конвейер на одной схеме).
 * Допущение: «Jev» = LLM-judge, оценка уверенности ответа.
 *
 * Виды:
 *   01-context     кто и с чем взаимодействует
 *   02-containers  из каких частей состоит система
 *   03-pipeline    главная схема: новость -> база -> запрос -> поиск -> ранжирование -> саммари -> Jev -> выдача
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

                queryBuilder = component "Запрос" "теги компании или вопрос чата" "Python"
                retriever = component "Retriever" "BM25 + векторы" "pgvector"
                ranker = component "Реранкер" "под профиль компании" "cross-encoder"
                profile = component "Профиль и граф" "признаки компании" "Python"
                orchestrator = component "Оркестратор" "LangGraph" "LangGraph"
                summarizer = component "Саммари LLM" "что и для кого" "LLM"
                claims = component "Claims и ссылки" "факт, банк, вывод, гипотеза" "Python"
                jev = component "Jev: уверенность" "LLM-judge" "LLM" "Judge"
            }
        }

        # контекст и контейнеры
        lpr -> ui "Читает баннеры, задаёт вопросы"
        ui -> core "Запросы с компанией и ролью"
        core -> db "Читает и пишет"
        news -> core "Новости"
        bankDocs -> core "PDF банка"
        core -> llm "Саммари и судья"

        # конвейер
        news -> sanitizer "Новости"
        sanitizer -> classifier "Чистый текст"
        classifier -> embedder "Теги"
        embedder -> db "Векторы и теги"

        ui -> queryBuilder "Вопрос чата или баннер"
        queryBuilder -> retriever "Запрос"
        db -> retriever "Кандидаты"
        retriever -> ranker "Кандидаты"
        profile -> ranker "Признаки компании"
        ranker -> orchestrator "Топ-K"
        orchestrator -> summarizer "Новости + профиль"
        summarizer -> claims "Черновик"
        claims -> jev "Утверждения + источники"
        jev -> ui "Высокая: резюме"
        jev -> orchestrator "Низкая: повтор или сама новость"
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
            include queryBuilder retriever ranker profile orchestrator summarizer claims jev
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
