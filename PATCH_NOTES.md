# Интеграция движка матчинга — что изменилось и как выкатить

## Изменённые/новые файлы (всё в репозитории)
1. **НОВОЕ** `src/matching/` — модуль движка:
   - `router.py` — роутинг по имени лота → карточка (топ-1000) или None (хвост)
   - `prompt_build.py` — адаптер: карточка/шпаргалка → БОЕВАЯ схема нормализации + `category`
   - `extractor.py` — лемматизация (pymorphy3)
   - `data/cards.json` (999 карточек), `data/product_types_full.csv` (словарь 4714 типов)
2. `src/process_specs.py` — +фича-флаг `MATCHING_MODE`, выбор промпта, `lot_name` в вызове (диф ниже)
3. `src/publish.py` — 1 строка: категория на витрину = `spec.get("category") or product_type`
4. `deploy/requirements.txt` — +`pymorphy3`

## Контракт НЕ сломан
Схема `structured_spec` та же: `{product_type, brand_required, brand, model, attributes[{name,value,unit,op}], search_query}` + новый ключ `category`. `search_verify.py`/`publish.py`/витрина работают как раньше.

## Фича-флаг (обязательно)
- `MATCHING_MODE=off` (по умолчанию) — поведение **1:1 как сейчас** (общий SYSTEM_PROMPT). Ничего не меняется.
- `MATCHING_MODE=on` — маршрутизация на карточный/универсальный промпт.
- Если модуль/`pymorphy3` не загрузится при `on` — **тихий откат** на общий промпт (воркер не падает).

## Выкатка (безопасная, поэтапная)
1. Влить файлы в git, `git pull` на сервере.
2. `bash deploy/sync.sh` — пересоберёт `app` (поставит `pymorphy3` из requirements). `src/` (воркеры) подхватятся git pull.
3. **Проверка на сервере, что модуль грузится (без включения на прод):**
   ```
   cd /opt/tenderview/tender-pipeline/src && python3 -c "from matching.prompt_build import build_for_tender; print(build_for_tender('Шина','Шина 205/55 R16')['mode'])"
   ```
   Ожидаем `card`.
4. **Тень/канарейка:** прогнать нормализацию на маленькой пачке с флагом, НЕ трогая общий поток:
   ```
   OLLAMA_API_KEY=... MATCHING_MODE=on LIMIT=20 python3 src/process_specs.py
   ```
   Сравнить `search_query`/`attributes`/`category` в БД со старыми (стенд, Фаза A).
5. Если метрики не хуже — включить `MATCHING_MODE=on` в `.env` и пробросить в `app` через `deploy/docker-compose.override.yml` (как другие env), затем `docker compose up -d --force-recreate app`.

## Откат
`MATCHING_MODE=off` (или убрать переменную) → следующий цикл воркера работает по-старому. Полный откат — `git revert`.

## Диф process_specs.py (суть)
- +блок флага/импорта после DELAY;
- `normalize(client, raw_spec, lot_name="")` — выбор `system` по флагу, `lot_name` в user-сообщение, `data["category"]=cat_hint` для карточек;
- в `main()`: `normalize(client, r["raw_spec"], r["name"])`.
