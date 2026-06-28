"""
ollama_price_probe.py — ЗАМЕР: сколько цен добавит поиск Олламы по «дыркам»
(лотам, которым мы сейчас НЕ даём цену), и сколько лимита Олламы это съедает.

Берёт N лотов, которые сейчас без цены (NOT_FOUND, либо найдены на тяжёлых
ozon/wildberries/yandex, где мы цену не берём). По каждому — ОДИН web_search
Олламы, достаёт цену из сниппетов (₸, либо руб -> ₸). Печатает таблицу и итог.

ВАЖНО про лимит: ПЕРЕД запуском и ПОСЛЕ посмотри "Session usage %" на
https://ollama.com/settings — разница и есть расход на N поисков.

Нужно: asyncpg, requests, DATABASE_URL, OLLAMA_API_KEY.
    python ollama_price_probe.py
    LIMIT=20 python ollama_price_probe.py
"""
import os
import re
import asyncio

import asyncpg
import requests

KEY = os.getenv("OLLAMA_API_KEY")
DB = os.getenv("DATABASE_URL")
N = int(os.getenv("LIMIT", "15"))
RUB_TO_KZT = 5.0
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def extract_price(text):
    """Первая осмысленная цена: ₸ (приоритет) или руб->₸. Возвращает (kzt, raw, валюта)."""
    for m in re.findall(r"(\d[\d\s\u2009\u00a0]{2,})\s*\u20b8", text or ""):
        c = re.sub(r"\D", "", m)
        if c.isdigit() and 100 < int(c) < 50_000_000:
            return int(c), int(c), "₸"
    for m in re.findall(r"(\d[\d\s\u2009\u00a0]{2,})\s*(?:руб|₽|р\.)", text or "", re.I):
        c = re.sub(r"\D", "", m)
        if c.isdigit() and 100 < int(c) < 50_000_000:
            return int(round(int(c) * RUB_TO_KZT)), int(c), "руб"
    return None, None, None


def ollama_search(q):
    try:
        r = requests.post("https://ollama.com/api/web_search", headers=H,
                          json={"query": q}, timeout=45)
        if r.status_code != 200:
            return []
        return r.json().get("results", [])
    except Exception:
        return []


async def main():
    if not (KEY and DB):
        print("Нужны OLLAMA_API_KEY и DATABASE_URL (--env-file + -e DATABASE_URL)")
        return

    conn = await asyncpg.connect(DB)
    rows = await conn.fetch("""
        SELECT name, price_per_unit, quantity, match_status, found_url
        FROM tenders
        WHERE stage = 'searched' AND is_closed = false
          AND (match_status IS NULL
               OR match_status = 'NOT_FOUND'
               OR found_url ILIKE '%ozon%'
               OR found_url ILIKE '%wildberries%'
               OR found_url ILIKE '%yandex%')
        ORDER BY collected_at DESC NULLS LAST
        LIMIT $1
    """, N)
    await conn.close()

    if not rows:
        print("Не нашёл подходящих лотов (без цены). Запусти поиск, чтобы появились данные.")
        return

    print(f"Лотов в замере (сейчас без цены): {len(rows)} | курс руб->₸: {RUB_TO_KZT}")
    print(">>> ПОСМОТРИ Session usage % на ollama.com/settings СЕЙЧАС (запиши число).\n")
    print(f"{'#':>2}  {'нашёл':5}  {'цена ₸':>10}  {'ориг':>11}  название")
    print("-" * 78)

    found = 0
    for i, r in enumerate(rows, 1):
        name = (r["name"] or "").strip()
        results = await asyncio.to_thread(ollama_search, f"{name} купить цена")
        kzt = raw = cur = None
        for res in results:
            kzt, raw, cur = extract_price(res.get("content", ""))
            if kzt:
                break
        if kzt:
            found += 1
            print(f"{i:>2}  {'ДА':5}  {kzt:>10}  {(str(raw)+' '+cur):>11}  {name[:40]}")
        else:
            print(f"{i:>2}  {'—':5}  {'':>10}  {'':>11}  {name[:40]}")

    pct = round(found / len(rows) * 100)
    print("-" * 78)
    print(f"\nИтог: цена найдена у {found} из {len(rows)} = {pct}% лотов, которые сейчас БЕЗ цены.")
    print(f"Сделано {len(rows)} поисков Олламы.")
    print(">>> Снова глянь Session usage % — разница = расход на эти "
          f"{len(rows)} поисков.")
    mult = round(3000 / len(rows))
    print(f"Прикидка: на ~3000 лотов/нед без цены это примерно {mult}× этого расхода.")


if __name__ == "__main__":
    asyncio.run(main())
