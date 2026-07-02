# TenderView — движок матчинга: все исправления (финальный бандл)

## Что внутри и куда класть (всё в дерево репозитория)

### НОВЫЙ модуль src/matching/
- router.py         — роутинг по имени лота → карточка (топ-1000) или None (хвост). ФИКС импорта (путь форсирован).
- prompt_build.py   — адаптер: карточка/шпаргалка → БОЕВАЯ схема нормализации + верификатор Вариант Б. ФИКС импорта.
- extractor.py      — лемматизация (pymorphy3).
- data/cards.json           — 999 карточек.
- data/product_types_full.csv — словарь 4714 типов.
- bench_compare.py  — тест-стенд (старый↔новый промпт, без записи в БД).

### ПРАВКИ существующих файлов
- src/process_specs.py — флаг MATCHING_MODE (нормализация карточками/универсальным), category, lot_name, traceback в откат.
- src/search_verify.py — флаг VERIFY_MODE (верификатор Вариант Б), traceback в откат.
- src/publish.py       — категория на витрину = spec.category or product_type (1 строка).
- src/daily_sync.py    — приём частичной выдачи ≥98% (SYNC_MIN_RATIO), не закрывая недобранные.
- loops/process_loop.sh — +pymorphy3 в инлайн-pip.
- loops/search_loop.sh  — +pymorphy3 в инлайн-pip.
- loops/full_seed.sh    — НОВЫЙ: разовый полный засев базы (холодный старт).
- deploy/requirements.txt — +pymorphy3.

## ГЛАВНЫЙ ФИКС этого бандла (критичный!)
Был баг: хрупкий `try/except ImportError` в prompt_build.py/router.py маскировал реальную ошибку
и выдавал ложное «No module named 'router'» → ОБА движка (MATCHING_MODE и VERIFY_MODE) молча
откатывались на старые промпты. Теперь путь модуля форсирован (sys.path.insert), импорт прямой,
+ печать настоящего traceback. После заливки этого бандла ОБА рычага реально включаются.

## ДВА ФЛАГА (в /opt/tenderview/.env, оба уже стоят on)
- MATCHING_MODE=on — улучшает ЗАПРОС (нормализация). Рычаг для «0 кандидатов».
- VERIFY_MODE=on   — улучшает ПРОВЕРКУ (Вариант Б). Рычаг для «отклонено при кандидатах» (9424 лота).
Оба по умолчанию off в коде; на сервере включены через .env. Откат — убрать строки из .env.

## SITES (площадки) — в .env
SITES=kaspi.kz,satu.kz,chipdip.kz,otvertka.kz,wildberries.ru,alibaba.com
(ozon/yandex/1688 убраны — с них не снять цену.)

## ВЫКАТКА
1. С НОУТА (git на сервере не настроен):
   git add -f src/matching src/process_specs.py src/search_verify.py src/publish.py src/daily_sync.py \
             loops/process_loop.sh loops/search_loop.sh loops/full_seed.sh deploy/requirements.txt PATCH_NOTES.md
   git commit -m "движок матчинга: карточки+универсальный+Вариант Б, фикс импорта, площадки, частичный сбор"
   git push
2. НА СЕРВЕРЕ:
   cd /opt/tenderview/tender-pipeline && git pull && bash deploy/sync.sh
   grep -c "sys.path.insert" src/matching/prompt_build.py     # ждём >=1 (фикс долетел)
3. ПЕРЕЗАПУСК воркеров:
   docker rm -f search_worker ollama_worker 2>/dev/null
   bash /opt/tenderview/process_loop.sh ; sleep 5 ; bash /opt/tenderview/search_loop.sh
4. ПРОВЕРКА (обе строки должны быть 🧩 ...on, НЕ «⚠️ откат»):
   docker logs ollama_worker 2>&1 | grep -m1 MATCHING_MODE
   docker logs search_worker 2>&1 | grep -m1 VERIFY_MODE
