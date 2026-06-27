#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Фоновый WB-проход (этап «цена WB») — СИНХРОННЫЙ (psycopg2), чтобы
не конфликтовать с синхронным браузером Playwright.

Берёт FOUND_EXACT WB-ссылки (живые, ещё без цены), тащит цену СТРОГО по
артикулу через wb_price.WBPriceFetcher (2 попытки) и пишет рублёвую цену
в match_result.price. publish.py потом сам переведёт ×5 в тенге и выведет
на витрину — править publish НЕ нужно.

Чтобы не висеть вечно на снятых/глухих товарах, считаем попытки в
match_result.wb_tries; после MAX_TRIES заходов ссылку больше не трогаем.

ENV: DATABASE_URL, WB_LIMIT (ссылок за запуск, 40), WB_ATTEMPTS (2),
     WB_MAX_TRIES (всего заходов на ссылку, 2).
"""
import os
import json
import time

import psycopg2

from wb_price import WBPriceFetcher

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
LIMIT = int(os.getenv("WB_LIMIT", "40"))
ATTEMPTS = int(os.getenv("WB_ATTEMPTS", "2"))
MAX_TRIES = int(os.getenv("WB_MAX_TRIES", "2"))

# %% — экранированный процент для psycopg2 (он использует %s для параметров)
SELECT_SQL = """
SELECT id, found_url, match_result
FROM tenders
WHERE match_status = 'FOUND_EXACT'
  AND COALESCE(found_url, match_result->>'source_url') ILIKE '%%wildberries.ru/catalog/%%'
  AND is_closed = false
  AND (deadline IS NULL OR deadline >= now())
  AND (match_result->>'price') IS NULL
  AND COALESCE((match_result->>'wb_tries')::int, 0) < %s
ORDER BY collected_at DESC
LIMIT %s
"""

UPDATE_SQL = "UPDATE tenders SET match_result = %s::jsonb WHERE id = %s"


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


def main():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(SELECT_SQL, (MAX_TRIES, LIMIT))
    rows = cur.fetchall()
    print("WB-проход: %d ссылок к обработке (лимит %d, попыток %d)" % (len(rows), LIMIT, ATTEMPTS))
    if not rows:
        cur.close()
        conn.close()
        return

    f = WBPriceFetcher(headless=True)
    got = 0
    try:
        for i, (rid, found_url, mr_raw) in enumerate(rows, 1):
            mr_dict = mr_raw
            if isinstance(mr_dict, str):
                try:
                    mr_dict = json.loads(mr_dict)
                except Exception:
                    mr_dict = {}
            url = found_url or (mr_dict or {}).get("source_url")
            t = time.time()
            price, note = f.fetch(url, attempts=ATTEMPTS)
            dt = time.time() - t
            new_mr = merge_result(mr_raw, price)
            cur.execute(UPDATE_SQL, (json.dumps(new_mr, ensure_ascii=False), rid))
            if price:
                got += 1
            shown = ("%s \u20bd" % price) if price else "\u2014"
            print("  [%d/%d] %9s  (%.0f\u0441, %s)  id=%s" % (i, len(rows), shown, dt, note, rid))
    finally:
        f._stop()
        cur.close()
        conn.close()
    print("\n\u0413\u043e\u0442\u043e\u0432\u043e: \u0446\u0435\u043d\u0430 \u0437\u0430\u043f\u0438\u0441\u0430\u043d\u0430 \u0443 %d \u0438\u0437 %d." % (got, len(rows)))


if __name__ == "__main__":
    main()
