#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Фоновый Alibaba-проход (этап «оптовый ориентир $») — СИНХРОННЫЙ (psycopg2).

НАСТРОЙКА ПО ИТОГАМ ЗАМЕРОВ: Alibaba отдаёт товары «через окно», которое
открывается/закрывается само. Когда окно полуоткрыто — товар дотягивается
УПОРНЫМИ попытками с паузой 15-20с между ними (в тестах так ловилось до 6/6,
а led bulb — на 5-й попытке). Поэтому:
  - до RETRIES попыток на тендер (по умолч. 8);
  - пауза МЕЖДУ ПОПЫТКАМИ 15-20с наугад (ключевое — короткие 3-5с ловили хуже);
  - без стоп-детекторов и второго круга (окно глобальное, добивать смысла нет —
    не пойманного добьёт следующий запуск по cron, когда окно откроется).

Идёт по живым подобранным тендерам без цены (предохранитель HARD_CAP на запуск).
Для каждого: английский ключ через Олламу -> упорный поиск Showroom ->
диапазон-ориентир (схема Б: медиана + коридор USD + MOQ) в ali_*.

В match_result.price кладём ориентир только если цены ещё нет (не затираем
точную тенге Kaspi/Satu). ali_tries — после MAX_TRIES заходов тендер пропускаем.

ENV: DATABASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL,
     ALI_RETRIES (попыток на тендер, 8),
     ALI_TRY_MIN/ALI_TRY_MAX (пауза между попытками, 15/20),
     ALI_GAP_MIN/ALI_GAP_MAX (пауза между тендерами, 8/12),
     ALI_MAX_TRIES (заходов на тендер за историю, 5),
     ALI_HARD_CAP (тендеров за один запуск, 40)
"""
import os
import json
import time
import random
import statistics

import psycopg2
from ollama import Client

from alibaba_price import AlibabaPriceFetcher

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
RETRIES = int(os.getenv("ALI_RETRIES", "8"))
TRY_MIN = float(os.getenv("ALI_TRY_MIN", "15"))
TRY_MAX = float(os.getenv("ALI_TRY_MAX", "20"))
GAP_MIN = float(os.getenv("ALI_GAP_MIN", "8"))
GAP_MAX = float(os.getenv("ALI_GAP_MAX", "12"))
MAX_TRIES = int(os.getenv("ALI_MAX_TRIES", "5"))
HARD_CAP = int(os.getenv("ALI_HARD_CAP", "40"))

SELECT_SQL = """
SELECT id, name, structured_spec, match_result
FROM tenders
WHERE match_status IN ('FOUND_EXACT','FOUND_PARTIAL')
  AND is_closed = false
  AND (deadline IS NULL OR deadline >= now())
  AND (match_result->>'price') IS NULL
  AND COALESCE((match_result->>'ali_tries')::int, 0) < %s
ORDER BY collected_at DESC
LIMIT %s
"""

UPDATE_SQL = "UPDATE tenders SET match_result = %s::jsonb WHERE id = %s"


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
    sq = (spec.get("search_query") or "").strip()
    if sq:
        return sq
    parts = [spec.get("product_type"), spec.get("brand"), spec.get("model")]
    return " ".join(str(x) for x in parts if x).strip() or (name or "").strip()


def en_keyword(client, ru):
    prompt = ("Translate the following product into a SHORT English search keyword "
              "(2-4 words) for the B2B wholesale marketplace Alibaba. Answer with ONLY "
              "the keyword, no quotes, no explanation.\n\nProduct: " + ru)
    try:
        resp = client.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}])
        txt = (resp["message"]["content"] or "").strip()
        return txt.splitlines()[0].strip().strip('"').strip()[:60]
    except Exception:
        return ""


def pct(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def build_orient(products):
    mins = sorted([p["usd_min"] for p in products if p.get("usd_min")])
    if not mins:
        return None
    moqs = [p.get("moq") for p in products if p.get("moq")]
    return {
        "price": round(statistics.median(mins), 2),
        "price_source": "alibaba",
        "price_currency": "USD",
        "ali_usd_low": round(pct(mins, 0.25), 2),
        "ali_usd_high": round(pct(mins, 0.75), 2),
        "ali_moq": moqs[0] if moqs else "",
        "ali_count": len(mins),
        "ali_sample": products[0]["title"][:60],
        "source_site": "alibaba.com",
    }


def merge_result(mr, orient):
    if isinstance(mr, str):
        try:
            mr = json.loads(mr)
        except Exception:
            mr = {}
    if not isinstance(mr, dict):
        mr = {}
    mr = dict(mr)
    mr["ali_tries"] = int(mr.get("ali_tries") or 0) + 1
    if orient:
        if mr.get("price") in (None, "", 0):
            mr.update(orient)
        else:
            for k in ("ali_usd_low", "ali_usd_high", "ali_moq", "ali_count", "ali_sample"):
                mr[k] = orient.get(k)
            mr["ali_orient_usd"] = orient.get("price")
    return mr


def search_persistent(fetcher, kw):
    """Упорная ловля окна: до RETRIES попыток, пауза 15-20с МЕЖДУ ними."""
    for att in range(1, RETRIES + 1):
        rows, _ = fetcher.search(kw, retries=1, delay=0)
        if rows:
            return rows, att
        if att < RETRIES:
            time.sleep(random.uniform(TRY_MIN, TRY_MAX))
    return [], RETRIES


def main():
    if not OLLAMA_API_KEY:
        print("Нет OLLAMA_API_KEY — проход не запущен.")
        return
    client = Client(host="https://ollama.com", headers={"Authorization": "Bearer " + OLLAMA_API_KEY})

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(SELECT_SQL, (MAX_TRIES, HARD_CAP))
    rows = cur.fetchall()
    print("Alibaba-проход: в очереди %d | до %d попыток/тендер, пауза между попытками %g-%gс"
          % (len(rows), RETRIES, TRY_MIN, TRY_MAX))
    if not rows:
        cur.close()
        conn.close()
        return

    fetcher = AlibabaPriceFetcher()
    got = 0
    processed = 0
    try:
        for i, (rid, name, spec_raw, mr_raw) in enumerate(rows, 1):
            spec = as_dict(spec_raw)
            ru = ru_source(name, spec)
            kw = en_keyword(client, ru)
            if not kw:
                cur.execute(UPDATE_SQL, (json.dumps(merge_result(mr_raw, None), ensure_ascii=False), rid))
                print("  [%d/%d] перевод не удался | id=%s" % (i, len(rows), rid))
                continue
            products, att = search_persistent(fetcher, kw)
            orient = build_orient(products) if products else None
            cur.execute(UPDATE_SQL, (json.dumps(merge_result(mr_raw, orient), ensure_ascii=False), rid))
            processed += 1
            if orient:
                got += 1
                print("  [%d/%d] %-34s | med $%.2f ($%.2f-$%.2f, от %s) | поп.%d | id=%s"
                      % (i, len(rows), kw[:34], orient["price"], orient["ali_usd_low"],
                         orient["ali_usd_high"], orient["ali_moq"], att, rid))
            else:
                print("  [%d/%d] %-34s | \u2014 окно закрыто (%d поп.) | id=%s"
                      % (i, len(rows), kw[:34], att, rid))
            if i < len(rows):
                time.sleep(random.uniform(GAP_MIN, GAP_MAX))
    finally:
        cur.close()
        conn.close()

    pctg = got * 100 // processed if processed else 0
    print("\nИтог: обработано %d, ориентир записан у %d (%d%%)." % (processed, got, pctg))


if __name__ == "__main__":
    main()
