#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alibaba_pricetest.py — СРАВНЕНИЕ трёх вариантов выбора цены Alibaba на живых
тендерах. ТОЛЬКО ЧИТАЕТ базу (никаких изменений).

По каждому тендеру: русское ТЗ -> английский ключ (Оллама) -> поиск Showroom ->
  А: Оллама выбирает самый похожий товар из списка -> его цена
  Б: диапазон-ориентир (25-я перцентиль .. 75-я, медиана) по всем товарам
  В: первый товар в выдаче

ENV: DATABASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL (gpt-oss:20b), N (тендеров, 6)

    python alibaba_pricetest.py
"""
import os
import json
import statistics

import psycopg2
from ollama import Client

from alibaba_price import AlibabaPriceFetcher

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
N = int(os.getenv("N", "6"))

SELECT_SQL = """
SELECT id, name, structured_spec
FROM tenders
WHERE structured_spec IS NOT NULL
  AND match_status IN ('FOUND_EXACT','FOUND_PARTIAL')
  AND is_closed = false
ORDER BY collected_at DESC
LIMIT %s
"""


def as_dict(v):
    if isinstance(v, dict):
        return v
    if not v:
        return {}
    try:
        return json.loads(v)
    except Exception:
        return {}


def ru_source(name, spec):
    """Что переводим: лучший доступный русский текст товара."""
    sq = (spec.get("search_query") or "").strip()
    if sq:
        return sq
    parts = [spec.get("product_type"), spec.get("brand"), spec.get("model")]
    alt = " ".join(str(x) for x in parts if x).strip()
    return alt or (name or "").strip()


def en_keyword(client, ru):
    """Оллама: короткий английский ключ для Alibaba."""
    prompt = (
        "Translate the following product into a SHORT English search keyword "
        "(2-4 words) for the B2B wholesale marketplace Alibaba. "
        "Answer with ONLY the keyword, no quotes, no explanation.\n\nProduct: " + ru
    )
    try:
        resp = client.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}])
        txt = (resp["message"]["content"] or "").strip()
        return txt.splitlines()[0].strip().strip('"').strip()[:60]
    except Exception as e:
        return "ERR:" + str(e)[:30]


def ollama_pick(client, ru, products):
    """Вариант А: Оллама выбирает индекс самого похожего товара (или -1)."""
    lst = "\n".join("%d) %s" % (i, p["title"][:70]) for i, p in enumerate(products))
    prompt = (
        "Russian product spec: " + ru + "\n\n"
        "Candidate wholesale products:\n" + lst + "\n\n"
        "Which candidate best matches the spec? Answer with ONLY the number. "
        "If none match, answer -1."
    )
    try:
        resp = client.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}])
        txt = (resp["message"]["content"] or "").strip()
        for tok in txt.replace(")", " ").split():
            if tok.lstrip("-").isdigit():
                idx = int(tok)
                if 0 <= idx < len(products):
                    return idx
                return -1
    except Exception:
        pass
    return -1


def pct(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def main():
    if not OLLAMA_API_KEY:
        print("Нет OLLAMA_API_KEY")
        return
    client = Client(host="https://ollama.com", headers={"Authorization": "Bearer " + OLLAMA_API_KEY})
    fetcher = AlibabaPriceFetcher()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(SELECT_SQL, (N,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    print("Тендеров к тесту: %d | модель: %s\n" % (len(rows), OLLAMA_MODEL))

    for rid, name, spec_raw in rows:
        spec = as_dict(spec_raw)
        ru = ru_source(name, spec)
        kw = en_keyword(client, ru)
        print("=" * 78)
        print("ТЕНДЕР: %s" % (name or "")[:70])
        print("  RU-источник : %s" % ru[:70])
        print("  EN-ключ     : %s" % kw)
        if kw.startswith("ERR:"):
            print("  (перевод не удался)\n")
            continue
        products, att = fetcher.search(kw, retries=4)
        print("  Showroom    : товаров=%d (попытка %d)" % (len(products), att))
        if not products:
            print("  (товаров нет — окно не поймали или ключ плохой)\n")
            continue

        mins = sorted([p["usd_min"] for p in products if p.get("usd_min")])
        # В — первый
        v = products[0]
        # А — выбор Олламы
        idx = ollama_pick(client, ru, products)
        a = products[idx] if idx >= 0 else None
        # Б — диапазон
        p25 = pct(mins, 0.25)
        p75 = pct(mins, 0.75)
        med = statistics.median(mins) if mins else None

        print("  --- ВАРИАНТЫ ЦЕНЫ ---")
        print("  А (Оллама выбрал): %s" %
              (("$%.2f | MOQ %s | %s" % (a["usd_min"], a["moq"], a["title"][:40])) if a else "ничего не подошло (-1)"))
        if med is not None:
            print("  Б (диапазон-ориентир): медиана $%.2f, типично $%.2f–$%.2f (по %d товарам)"
                  % (med, p25, p75, len(mins)))
        print("  В (первый в выдаче): $%.2f | MOQ %s | %s"
              % (v["usd_min"] or 0, v["moq"], v["title"][:40]))
        print()


if __name__ == "__main__":
    main()
