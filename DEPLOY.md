# Деплой и миграция

Как развернуть чистый репозиторий на сервере, не сломав работающую систему.

> **Важно про топологию.** Репозиторий клонируется в
> `/opt/tenderview/tender-pipeline` (как и раньше). Папка `/opt/tenderview`
> остаётся «боевой» (runtime): там лежат `.env`, логи воркеров и туда
> `deploy/sync.sh` раскладывает веб-приложение, конфиг и cron-обёртки.
> Воркеры пайплайна (`src/`) cron запускает прямо из репозитория — поэтому
> для обновления логики достаточно `git pull` (без пересборки).

---

## 0. Перед началом

- Сделан полный бэкап (есть архив `tenderview_backup_*.tar.gz`).
- Под рукой содержимое `/opt/tenderview/.env` (секреты) — он НЕ в git и
  останется на сервере нетронутым.
- Бот MetaTrader на этом же сервере живёт отдельно (vncserver, не в
  `/opt/tenderview`, не в Docker) — миграция его не касается.

---

## 1. Залить чистый репозиторий (с ПК, PowerShell)

В папке локального репозитория, заменив всё содержимое на файлы из архива:

```powershell
cd "C:\Users\magzh\Desktop\Magzhan Проекты\3. Тендер ++\Olloma\tender-pipeline"

# удалить старое содержимое (кроме .git), затем распаковать чистый репозиторий поверх
Get-ChildItem -Force | Where-Object { $_.Name -ne '.git' } | Remove-Item -Recurse -Force

# распакуй сюда содержимое присланного архива tenderview_clean.zip, затем:
git add -A
git commit -m "Чистая структура репозитория: src/web/loops/deploy/archive"
git push
```

---

## 2. Обновить репозиторий на сервере

```bash
cd /opt/tenderview/tender-pipeline
git pull
ls          # проверь, что появились папки src/ web/ loops/ deploy/ archive/
```

---

## 3. Разложить по боевым местам и пересобрать

```bash
cd /opt/tenderview/tender-pipeline
bash deploy/sync.sh
```

Скрипт скопирует `web/`, `deploy/`, `loops/` в `/opt/tenderview`, сделает
loop-скрипты исполняемыми и пересоберёт контейнер `app`. `.env` не трогается.

---

## 4. Обновить cron

Старые задачи `run_collect.sh`, `sync_status_loop.sh`, `load_loop.sh` больше
не нужны — их заменил `daily_sync_loop.sh` (сверка с goszakup + новые лоты +
синхронизация статусов). Загрузка лотов теперь идёт прямо из `daily_sync`
(минуя CSV), а определение «закрыт» — по присутствию в goszakup, без вредной
строки закрытия по дедлайну, которая раньше была в `load_loop.sh`.

Привести crontab к такому виду (строку бота `@reboot ... vncserver` НЕ трогать):

```cron
@reboot sleep 10 && vncserver :1 -geometry 1280x800 -depth 24
0  */4 * * * /opt/tenderview/daily_sync_loop.sh
*/10 * * * * /opt/tenderview/process_loop.sh
*/15 * * * * /opt/tenderview/search_loop.sh
*/15 * * * * /opt/tenderview/publish_loop.sh
0  *   * * * /opt/tenderview/wb_loop.sh
*/30 * * * * /opt/tenderview/alibaba_loop.sh
```

Редактирование: `crontab -e` (или заменить целиком). Сохрани бэкап текущего:
`crontab -l > /tmp/cron.bak`.

---

## 5. Проверка после деплоя

```bash
# контейнеры живы
docker compose ps

# витрина и панель отвечают (ожидаем HTTP 200)
curl -s -o /dev/null -w "%{http_code}\n" https://$DOMAIN/
curl -s -o /dev/null -w "%{http_code}\n" https://$DOMAIN/admin

# бот MetaTrader не задет
vncserver -list
```

Затем открой **/admin** — блок «Воркеры» покажет, что каждая задача отрабатывает
(после первого запуска по расписанию). Если воркер красный — проверь его лог
в `/opt/tenderview/<имя>.log`.

---

## Откат

Если что-то пошло не так — восстановить из бэкапа:

```bash
cd /opt/tenderview
tar -xzf /путь/к/tenderview_backup_*.tar.gz
docker compose up -d --build app
crontab /tmp/cron.bak
```
