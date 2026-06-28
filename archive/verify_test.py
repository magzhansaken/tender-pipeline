#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_test.py — прогоняем НАСТОЯЩИЙ конвейер поиска+верификации на NOT_FOUND
лотах и смотрим, ЧТО Оллама решает и ПОЧЕМУ бракует. Переиспользует функции из
search_verify.py, ничего в базу не пишет.
"""
import os
import asyncio

import asyncpg
from ollama import Client

import search_verify as sv

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
N = int(os.getenv("N", "4"))


async def main():
    if not OLLAMA_API_KEY:
        print("Нет OLLAMA_API_KEY")
        return
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch(
        "SELECT lot_number, name, structured_spec FROM tenders "
        "WHERE is_closed=false AND match_status='NOT_FOUND' "
        "AND lot_number ~ '-ЗЦП([2-9]|[1-9][0-9])' "
        "ORDER BY random() LIMIT $1", N)
    client = Client(host="https://ollama.com",
                    headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"})

    for r in rows:
        print("=" * 66)
        print("ТОВАР: %s  [%s]" % (r["name"][:42], r["lot_number"]))
        anketa = sv.load_anketa(r["structured_spec"])
        queries = sv.build_queries(anketa)
        print("  запросы: %s" % queries)
        try:
            cands = await asyncio.to_thread(sv.ddgs_search, queries)
        except Exception as e:
            print("  поиск упал: %s" % e)
            continue
        print("  кандидатов найдено: %d" % len(cands))
        if not cands:
            print("  -> 0 кандидатов, поэтому NOT_FOUND (поиск ничего не дал)")
            print()
            continue
        ranked = sv.rank_candidates(cands, queries)
        try:
            v = await asyncio.to_thread(sv.verify, client, anketa, ranked)
        except Exception as e:
            print("  верификация упала: %s" % e)
            continue
        print("  >>> ВЕРДИКТ Олламы: %s (confidence %s)" % (v.get("status"), v.get("confidence")))
        print("      причина: %s" % v.get("reason"))
        print("      подтвердилось: %s" % v.get("matched_specs"))
        print("      НЕ подтвердилось: %s" % v.get("missing_specs"))
        print("      конфликты: %s" % v.get("conflicts"))
        print("  топ-3 кандидата, что видела Оллама:")
        for c in ranked[:3]:
            print("     - %s" % c.get("title", "")[:62])
            print("       %s" % c.get("url", "")[:58])
        print()

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
