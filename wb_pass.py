#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Фоновый WB-проход (этап «цена WB»).

Берёт FOUND_EXACT WB-ссылки (живые, ещё без цены), тащит цену СТРОГО по
артикулу через wb_price.WBPriceFetcher (2 попытки на ссылку) и пишет
рублёвую цену в match_result.price. publish.py потом сам переведёт ×5 в
тенге и выведет на витрину — править publish НЕ нужно.

Чтобы не висеть вечно на снятых/глухих товарах, считаем попытки в
match_result.wb_tries и после MAX_TRIES заходов ссылку больше не трогаем.

ENV: DATABASE_URL, WB_LIMIT (сколько ссылок за один запуск, по умолч. 40),
     WB_ATTEMPTS (повторы при антиботе, 2), WB_MAX_TRIES (заходов всего, 2).
"""
import os
import json
import time
import asyncio

import asyncpg

from wb_price import WBPriceFetcher

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
LIMIT = int(os.getenv("WB_LIMIT", "40"))
ATTEMPTS = int(os.getenv("WB_ATTEMPTS", "2"))
MAX_TRIES = int(os.getenv("WB_MAX_TRIES", "2"))

SELECT_SQL = """
SELECT id, found_url, match_result
FROM tenders
WHERE match_status = 'FOUND_EXACT'
  AND COALESCE(found_url, match_result->>'source_url') ILIKE '%wildberries.ru/catalog/%'
  AND is_closed = false
  AND (deadline IS NULL OR deadline >= now())
  AND (match_result->>'price') IS NULL
  AND COALESCE((match_result->>'wb_tries')::int, 0) < $1
ORDER BY collected_at DESC
LIMIT $2
"""


def merge_result(mr, price):
    """Чистая логика обновления match_result. Возврат нового dict."""
    if isinstance(mr, str):
        try:
            mr = json.loads(mr)
        except Exception:
            mr = {}
    if not isinstance(mr, dict):
        mr = {}
    mr = dict(mr)
    mr["wb_tries"] = int(mr.get("wb_tries") or 0) + 1
    if price:
        mr["price"] = price
        mr["price_source"] = "wb"
        mr["price_currency"] = "RUB"
    return mr


async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch(SELECT_SQL, MAX_TRIES, LIMIT)
    print(f"WB-проход: {len(rows)} ссылок к обработке (лимит {LIMIT}, попыток {ATTEMPTS})")
    if not rows:
        await conn.close()
        return
    f = WBPriceFetcher(headless=True)
    got = 0
    try:
        for i, r in enumerate(rows, 1):
            mr_raw = r["match_result"]
            mr_dict = mr_raw
            if isinstance(mr_dict, str):
                mr_dict = json.loads(mr_dict)
            url = r["found_url"] or (mr_dict or {}).get("source_url")
            t = time.time()
            price, note = f.fetch(url, attempts=ATTEMPTS)
            dt = time.time() - t
            new_mr = merge_result(mr_raw, price)
            await conn.execute(
                "UPDATE tenders SET match_result = $1::jsonb WHERE id = $2",
                json.dumps(new_mr, ensure_ascii=False), r["id"],
            )
            if price:
                got += 1
            shown = f"{price} \u20bd" if price else "\u2014"
            print(f"  [{i}/{len(rows)}] {shown:>9}  ({dt:.0f}\u0441, {note})  id={r['id']}")
    finally:
        f._stop()
        await conn.close()
    print(f"\n\u0413\u043e\u0442\u043e\u0432\u043e: \u0446\u0435\u043d\u0430 \u0437\u0430\u043f\u0438\u0441\u0430\u043d\u0430 \u0443 {got} \u0438\u0437 {len(rows)}.")


if __name__ == "__main__":
    asyncio.run(main())
