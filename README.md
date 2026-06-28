# TenderView

Автоматический поиск и оценка тендеров госзакупок Казахстана (goszakup.gov.kz).

Конвейер собирает лоты (товары) с goszakup, извлекает требования из ТЗ,
подбирает товар на маркетплейсах, считает цену и маржу и публикует результат
на читаемую витрину.

---

## Как это работает (поток данных)

```
goszakup.gov.kz
      │  (1) daily_sync.py  — сверка с goszakup, новые лоты + синхронизация статусов
      ▼
   tenders (БД, stage='collected')
      │  (2) process_specs.py  — Ollama извлекает характеристики + поисковый запрос
      ▼
   stage='parsed'
      │  (3) search_verify.py  — поиск на площадках (DuckDuckGo) + сверка через Ollama
      │       └─ price_fetch.py — цена с лёгких KZ-площадок (Kaspi/Satu/Chipdip/Otvertka)
      ▼
   stage='searched', match_status=FOUND_*/NOT_FOUND
      │  (4) wb_pass.py / alibaba_pass.py — добор цен (Wildberries ₽, Alibaba опт-$)
      │       fx_rate.py — живые курсы Нацбанка РК
      ▼
      │  (5) publish.py  — расчёт маржи, публикация
      ▼
   lots (БД, витрина только читает)
      │
      ▼
   app.py (FastAPI)  →  витрина (/) и админ-панель (/admin)
```

**Определение «живой/закрытый» лот** — по факту присутствия в активной выдаче
goszakup (статус 240 «приём ценовых предложений»), а **НЕ по дате дедлайна**:
у переобъявленных лотов (ЗЦП2/ЗЦП3…) на странице висит срок старого этапа,
поэтому дате верить нельзя. Источник истины — сам goszakup.

---

## Структура репозитория

```
tender-pipeline/
├── src/                    # рабочий код пайплайна (воркеры)
│   ├── techspec_dumper.py  #   парсер goszakup (класс GoszakupParser, скачивание ТЗ из PDF)
│   ├── daily_sync.py       #   ГЛАВНЫЙ: сверка с goszakup → новые лоты + статусы
│   ├── load_tenders.py     #   загрузчик CSV → БД (легаси-путь; daily_sync пишет в БД напрямую)
│   ├── process_specs.py    #   нормализация ТЗ через Ollama (характеристики + search_query)
│   ├── search_verify.py    #   поиск товара на площадках + верификация через Ollama
│   ├── price_fetch.py      #   цена с KZ-площадок (Kaspi/Satu/Chipdip/Otvertka)
│   ├── publish.py          #   расчёт маржи и публикация в витрину
│   ├── fx_rate.py          #   живые курсы валют Нацбанка РК
│   ├── wb_pass.py          #   проход добора цен Wildberries (₽-ориентир)
│   ├── wb_price.py         #   класс получения цены WB
│   ├── alibaba_pass.py     #   проход добора оптовых ориентиров Alibaba ($)
│   └── alibaba_price.py    #   класс получения цены Alibaba
│
├── web/                    # веб-слой
│   ├── app.py              #   FastAPI: API витрины + админ-панель
│   ├── load_results.py     #   вспомогательная загрузка результатов
│   └── static/
│       ├── index.html      #   витрина (публичная, read-only)
│       └── admin.html      #   панель управления (за паролем)
│
├── loops/                  # cron-обёртки (запускают воркеры по расписанию)
│   ├── daily_sync_loop.sh  #   каждые 4 часа
│   ├── process_loop.sh     #   каждые 10 мин
│   ├── search_loop.sh      #   каждые 15 мин
│   ├── publish_loop.sh     #   каждые 15 мин
│   ├── wb_loop.sh          #   каждый час
│   └── alibaba_loop.sh     #   каждые 30 мин
│
├── deploy/                 # инфраструктура
│   ├── Dockerfile          #   образ веб-приложения
│   ├── docker-compose.yml  #   db (postgres) + app (FastAPI) + caddy (TLS-прокси)
│   ├── docker-compose.override.yml  # пароль админки + монтаж логов в контейнер
│   ├── requirements.txt    #   зависимости веб-приложения
│   ├── Caddyfile           #   reverse-proxy на app:8000
│   ├── schema.sql          #   схема таблицы lots (инициализация БД)
│   └── sync.sh             #   деплой: раскладывает репозиторий по боевым местам
│
├── archive/                # пробы, тупиковые ветки, тесты, легаси (НЕ используются)
│
├── .env.example            # шаблон секретов (реальный .env не в git)
├── .gitignore
├── README.md               # этот файл
└── DEPLOY.md               # пошаговый деплой и миграция
```

---

## Площадки (источники цен)

| Площадка     | Статус | Что даёт |
|--------------|--------|----------|
| Kaspi.kz     | ✅ работает | тенге, точная цена |
| Satu.kz      | ✅ работает | тенге |
| Chipdip.kz   | ✅ работает | тенге (электроника) |
| Otvertka.kz  | ✅ работает | тенге |
| Wildberries  | ✅ работает | рубли (ориентир) |
| Alibaba      | ✅ работает | опт-$ (ориентир, через Showroom) |
| Ozon         | ⛔ бан IP   | нужен резидентный прокси / офиц. API |
| AliExpress   | ⛔ бан IP   | то же |

---

## Расписание (cron)

```
0  */4 * * *   daily_sync_loop.sh    # сверка с goszakup + новые лоты + статусы
*/10 * * * *   process_loop.sh       # нормализация Ollama
*/15 * * * *   search_loop.sh        # поиск на площадках
*/15 * * * *   publish_loop.sh       # публикация в витрину
0  *   * * *   wb_loop.sh            # добор цен Wildberries
*/30 * * * *   alibaba_loop.sh       # добор ориентиров Alibaba
```

Каждый воркер защищён от двойного запуска (проверка running-контейнера) и сам
завершается, когда обработал свою пачку.

---

## Деплой

Полная инструкция — в **DEPLOY.md**. Кратко:

```bash
# на сервере, после git pull репозитория:
bash deploy/sync.sh        # разложит web/, loops/, deploy/ по боевым местам и пересоберёт app
```

Секреты (пароли БД, ключ Ollama, пароль админки) лежат в `/opt/tenderview/.env`
и в git не попадают.
