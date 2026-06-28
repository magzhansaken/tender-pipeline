#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""search_test.py — проверяем гипотезу: NOT_FOUND из-за перегруженных запросов.
Берём переобъявленные NOT_FOUND лоты, прогоняем через тот же DuckDuckGo
ТЕКУЩИЙ запрос vs ОЧИЩЕННЫЙ (без количества/объёма партии/дат) и считаем,
сколько результатов находит каждый на Kaspi и Wildberries.
"""
import os
import re
import time

import psycopg2
from ddgs import DDGS

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
SITES = ["kaspi.kz", "wildberries.ru"]
N = int(os.getenv("N", "6"))


def ddg_count(query):
    out = {}
    for site in SITES:
        try:
            res = DDGS().text(f"site:{site} {query}", region="ru-ru", max_results=10)
            out[site] = len(res) if res else 0
        except Exception as e:
            out[site] = "ошибка(%s)" % str(e)[:25]
        time.sleep(2)
    return out


def clean_query(q):
    """Убираем из запроса то, что относится к ЗАКУПКЕ, а не к товару:
    объём/количество партии и даты. Характеристики НЕ трогаем."""
    s = q
    s = re.sub(r"\d{4}-\d{2}-\d{2}", " ", s)                       # даты 2026-09-30
    s = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b", " ", s)           # даты 30.09.2026
    s = re.sub(r"\b\d{1,4}\s*[-–]\s*\d{1,4}\s*(л|кг|шт|мл|листов|т)\b", " ", s, flags=re.I)  # 10-20 л
    s = re.sub(r"\b\d{3,}\s*(л|шт|листов|мл)\b", " ", s, flags=re.I)  # 1120 л, 500 листов
    s = re.sub(r"\bталон\w*\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        "SELECT name, structured_spec->>'search_query', structured_spec->>'product_type' "
        "FROM tenders WHERE is_closed=false AND match_status='NOT_FOUND' "
        "AND lot_number ~ '-ЗЦП([2-9]|[1-9][0-9])' ORDER BY random() LIMIT %s", (N,))
    rows = cur.fetchall()
    cur.close(); conn.close()

    for name, sq, pt in rows:
        sq = sq or ""
        print("=" * 64)
        print("ТОВАР: %s" % name[:42])
        print("  запрос:  %s" % sq)
        cur_res = ddg_count(sq)
        print("  ТЕКУЩИЙ  -> %s" % cur_res)
        cq = clean_query(sq)
        if cq and cq != sq:
            print("  очищен:  %s" % cq)
            cln_res = ddg_count(cq)
            print("  ОЧИЩЕННЫЙ-> %s" % cln_res)
        else:
            print("  (очистка ничего не убрала — запрос и так чистый)")
            if pt and pt != sq:
                print("  тип товара: %s" % pt)
                print("  ТОЛЬКО ТИП-> %s" % ddg_count(pt))
        print()


if __name__ == "__main__":
    main()
