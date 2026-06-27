#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Фоновый Alibaba-проход (этап «оптовый ориентир $») — СИНХРОННЫЙ (psycopg2).

Берёт живые тендеры БЕЗ цены, делает английский ключ через Олламу, ищет в
Alibaba Showroom (alibaba_price, с ретраями — Alibaba отдаёт «через раз»),
и пишет в match_result ДИАПАЗОН-ОРИЕНТИР (схема Б): медиана + типичный
коридор цен USD + минимальный MOQ. publish.py переведёт USD->тенге и пометит.

Alibaba нестабилен с серверного IP (~1/3 запросов), поэтому:
  - паузы между тендерами (ALI_GAP сек), чтобы не жечь «бюджет доверия»;
  - счётчик ali_tries в match_result; после ALI_MAX_TRIES тендер не трогаем
    (иначе вечно бьёмся в те, что не ловятся).

Пишем В ОТДЕЛЬНЫЕ поля (ali_*), НЕ перетираем основной матч с маркетплейса.
Цена-ориентир кладётся в match_result.price ТОЛЬКО если её там ещё нет
(чтобы не затирать точную тенге-цену с Kaspi/Satu).

ENV: DATABASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL (gpt-oss:20b),
     ALI_LIMIT (тендеров за запуск, 15), ALI_RETRIES (попыток поиска, 5),
     ALI_GAP (пауза между тендерами сек, 20), ALI_MAX_TRIES (заходов на тендер, 2)
"""
import os
import json
import time
import statistics

import psycopg2
from ollama import Client

from alibaba_price import AlibabaPriceFetcher

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
LIMIT = int(os.getenv("ALI_LIMIT", "15"))
RETRIES = int(os.getenv("ALI_RETRIES", "5"))
GAP = int(os.getenv("ALI_GAP", "20"))
MAX_TRIES = int(os.getenv("ALI_MAX_TRIES", "2"))

# Живые подобранные тендеры без цены, у кого ali-попыток меньше лимита.
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
    alt = " ".join(str(x) for x in parts if x).strip()
    return alt or (name or "").strip()


def en_keyword(client, ru):
    prompt = (
        "Translate the following product into a SHORT English search keyword "
        "(2-4 words) for the B2B wholesale marketplace Alibaba. "
        "Answer with ONLY the keyword, no quotes, no explanation.\n\nProduct: " + ru
    )
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
    """Схема Б: диапазон-ориентир. Возврат dict для записи или None."""
    mins = sorted([p["usd_min"] for p in products if p.get("usd_min")])
    if not mins:
        return None
    med = statistics.median(mins)
    p25 = pct(mins, 0.25)
    p75 = pct(mins, 0.75)
    # минимальный MOQ среди товаров (самый доступный)
    moqs = [p.get("moq") for p in products if p.get("moq")]
    moq = moqs[0] if moqs else ""
    sample = products[0]["title"][:60]
    return {
        "price": round(med, 2),                 # ориентир-цена = медиана USD
        "price_source": "alibaba",
        "price_currency": "USD",
        "ali_usd_low": round(p25, 2),
        "ali_usd_high": round(p75, 2),
        "ali_moq": moq,
        "ali_count": len(mins),
        "ali_sample": sample,
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
        # не перетираем уже существующую цену (точную тенге с Kaspi/Satu и т.п.)
        if mr.get("price") in (None, "", 0):
            mr.update(orient)
        else:
            # цена уже есть — кладём ориентир Alibaba в отдельные поля, не трогая price
            for k in ("ali_usd_low", "ali_usd_high", "ali_moq", "ali_count", "ali_sample"):
                mr[k] = orient.get(k)
            mr["ali_orient_usd"] = orient.get("price")
    return mr


def main():
    if not OLLAMA_API_KEY:
        print("Нет OLLAMA_API_KEY — проход не запущен.")
        return
    client = Client(host="https://ollama.com", headers={"Authorization": "Bearer " + OLLAMA_API_KEY})

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(SELECT_SQL, (MAX_TRIES, LIMIT))
    rows = cur.fetchall()
    print("Alibaba-проход: %d тендеров (лимит %d, ретраев %d, пауза %dс)"
          % (len(rows), LIMIT, RETRIES, GAP))
    if not rows:
        cur.close()
        conn.close()
        return

    fetcher = AlibabaPriceFetcher()
    got = 0
    try:
        for i, (rid, name, spec_raw, mr_raw) in enumerate(rows, 1):
            spec = as_dict(spec_raw)
            ru = ru_source(name, spec)
            kw = en_keyword(client, ru)
            if not kw:
                new_mr = merge_result(mr_raw, None)
                cur.execute(UPDATE_SQL, (json.dumps(new_mr, ensure_ascii=False), rid))
                print("  [%d/%d] перевод не удался | id=%s" % (i, len(rows), rid))
                continue
            t = time.time()
            products, att = fetcher.search(kw, retries=RETRIES, delay=3.0)
            orient = build_orient(products) if products else None
            new_mr = merge_result(mr_raw, orient)
            cur.execute(UPDATE_SQL, (json.dumps(new_mr, ensure_ascii=False), rid))
            dt = time.time() - t
            if orient:
                got += 1
                shown = "med $%.2f ($%.2f-$%.2f, от %s)" % (
                    orient["price"], orient["ali_usd_low"], orient["ali_usd_high"], orient["ali_moq"])
            else:
                shown = "\u2014 (нет товаров)"
            print("  [%d/%d] %-40s | %s | %.0fс id=%s" % (i, len(rows), kw[:40], shown, dt, rid))
            if i < len(rows):
                time.sleep(GAP)
    finally:
        cur.close()
        conn.close()
    print("\nГотово: ориентир записан у %d из %d." % (got, len(rows)))


if __name__ == "__main__":
    main()
