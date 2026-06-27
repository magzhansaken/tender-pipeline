#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Фоновый Alibaba-проход (этап «оптовый ориентир $») — СИНХРОННЫЙ (psycopg2).

ВАЖНО (проверено замерами): Alibaba отдаёт товары «через раз» — окно открывается
не всегда, и его надо УПОРНО ловить (иногда нужно 6 попыток). «0 товаров» с
малым числом попыток — это НЕ «товара нет» и НЕ «квота кончилась», а просто
«в этот момент окно было закрыто». Поэтому:
  - на каждый тендер до RETRIES попыток (по умолч. 8) с паузой DELAY±разброс;
  - НЕТ авто-стопа по «серии отказов» (он давал ложную остановку);
  - кого не поймали в первом круге — добиваем ВТОРЫМ кругом в конце.

Идёт по ВСЕМ живым подобранным тендерам без цены (жёсткого лимита нет, только
предохранитель HARD_CAP). Для каждого: английский ключ через Олламу -> поиск
Showroom -> диапазон-ориентир (схема Б: медиана + коридор USD + MOQ) в ali_*.

В match_result.price ориентир кладётся только если цены ещё нет (не затираем
точную тенге-цену Kaspi/Satu). ali_tries — после MAX_TRIES заходов на тендер в
РАЗНЫХ запусках больше не берём (чтобы не копить вечно ненаходимые).

ENV: DATABASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL (gpt-oss:20b),
     ALI_RETRIES (попыток на тендер, 8), ALI_DELAY (пауза внутри попыток, 5),
     ALI_GAP_MIN/ALI_GAP_MAX (пауза между тендерами, 15/20),
     ALI_SECOND_ROUND (1=добивать непойманных вторым кругом, 1),
     ALI_MAX_TRIES (заходов на тендер за всю историю, 3),
     ALI_HARD_CAP (предохранитель на один запуск, 300)
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
DELAY = float(os.getenv("ALI_DELAY", "5"))
GAP_MIN = float(os.getenv("ALI_GAP_MIN", "15"))
GAP_MAX = float(os.getenv("ALI_GAP_MAX", "20"))
SECOND_ROUND = os.getenv("ALI_SECOND_ROUND", "1") == "1"
MAX_TRIES = int(os.getenv("ALI_MAX_TRIES", "3"))
HARD_CAP = int(os.getenv("ALI_HARD_CAP", "300"))

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
    """Упорный поиск: до RETRIES попыток с паузой DELAY +-разброс (ловим окно)."""
    for _ in range(RETRIES):
        rows, _att = fetcher.search(kw, retries=1, delay=0)
        if rows:
            return rows
        time.sleep(DELAY + random.uniform(-1.5, 1.5))
    return []


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
    print("Alibaba-проход: в очереди %d | до %d попыток/тендер | пауза %g-%gс наугад%s"
          % (len(rows), RETRIES, GAP_MIN, GAP_MAX, " | + второй круг" if SECOND_ROUND else ""))
    if not rows:
        cur.close()
        conn.close()
        return

    fetcher = AlibabaPriceFetcher()
    got = 0
    processed = 0
    missed = []  # (rid, kw, mr_raw) — не пойманные в первом круге
    try:
        # ===== ПЕРВЫЙ КРУГ =====
        for i, (rid, name, spec_raw, mr_raw) in enumerate(rows, 1):
            spec = as_dict(spec_raw)
            ru = ru_source(name, spec)
            kw = en_keyword(client, ru)
            if not kw:
                cur.execute(UPDATE_SQL, (json.dumps(merge_result(mr_raw, None), ensure_ascii=False), rid))
                print("  [%d/%d] перевод не удался | id=%s" % (i, len(rows), rid))
                continue
            products = search_persistent(fetcher, kw)
            orient = build_orient(products) if products else None
            if orient:
                cur.execute(UPDATE_SQL, (json.dumps(merge_result(mr_raw, orient), ensure_ascii=False), rid))
                processed += 1
                got += 1
                print("  [%d/%d] %-36s | med $%.2f ($%.2f-$%.2f, от %s) | id=%s"
                      % (i, len(rows), kw[:36], orient["price"], orient["ali_usd_low"],
                         orient["ali_usd_high"], orient["ali_moq"], rid))
            else:
                missed.append((rid, kw, mr_raw))
                print("  [%d/%d] %-36s | \u2014 первый круг мимо | id=%s" % (i, len(rows), kw[:36], rid))
            if i < len(rows):
                time.sleep(random.uniform(GAP_MIN, GAP_MAX))

        # ===== ВТОРОЙ КРУГ (добиваем непойманных) =====
        if SECOND_ROUND and missed:
            print("\n--- Второй круг: добиваем %d непойманных ---" % len(missed))
            still = []
            for j, (rid, kw, mr_raw) in enumerate(missed, 1):
                products = search_persistent(fetcher, kw)
                orient = build_orient(products) if products else None
                cur.execute(UPDATE_SQL, (json.dumps(merge_result(mr_raw, orient), ensure_ascii=False), rid))
                processed += 1
                if orient:
                    got += 1
                    print("  (2) [%d/%d] %-34s | med $%.2f | id=%s"
                          % (j, len(missed), kw[:34], orient["price"], rid))
                else:
                    still.append(rid)
                    print("  (2) [%d/%d] %-34s | \u2014 не нашли | id=%s" % (j, len(missed), kw[:34], rid))
                if j < len(missed):
                    time.sleep(random.uniform(GAP_MIN, GAP_MAX))
            print("После второго круга осталось без цены: %d" % len(still))
        else:
            # второй круг выключен — всё равно отметим попытку у непойманных
            for rid, kw, mr_raw in missed:
                cur.execute(UPDATE_SQL, (json.dumps(merge_result(mr_raw, None), ensure_ascii=False), rid))
                processed += 1
    finally:
        cur.close()
        conn.close()

    print("\nИтог: обработано %d, ориентир записан у %d." % (processed, got))


if __name__ == "__main__":
    main()
