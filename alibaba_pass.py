#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Фоновый Alibaba-проход (этап «оптовый ориентир $») — СИНХРОННЫЙ (psycopg2).

Идёт по ВСЕМ живым подобранным тендерам без цены (жёсткого лимита в коде нет),
для каждого: английский ключ через Олламу -> поиск Alibaba Showroom (с ретраями)
-> диапазон-ориентир (схема Б, медиана + коридор USD + MOQ) в отдельные поля.

ПАУЗА между тендерами — СЛУЧАЙНАЯ 15-20с (меньше похоже на бота; и не жжёт
«бюджет доверия» Alibaba залпом).

ДЕТЕКТОР ОСТАНОВКИ: Alibaba даёт ограниченное число запросов с нашего IP за
период (дневная квота, паузой не лечится). Когда квота кончается — идут отказы
подряд. Если подряд STOP_AFTER отказов (по умолч. 5) — значит квота исчерпана,
дальше долбить бессмысленно: проход пишет в лог и КОРРЕКТНО выходит. Следующий
запуск (по cron) продолжит — за это время квота частично восстановится.

Счётчик ali_tries в match_result: после MAX_TRIES заходов тендер не трогаем
(чтобы при живой квоте не залипать на тех, что не находятся в принципе).

Пишем ориентир в ОТДЕЛЬНЫЕ поля ali_*. В слот match_result.price кладём только
если цены ещё нет (чтобы не затереть точную тенге-цену Kaspi/Satu). Маржу из
этого ориентира publish.py считать НЕ должен (доллар-опт ≠ закуп) — это решается
на стороне publish.py отдельно.

ENV: DATABASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL (gpt-oss:20b),
     ALI_RETRIES (попыток поиска, 5), ALI_GAP_MIN/ALI_GAP_MAX (пауза, 15/20),
     ALI_STOP_AFTER (отказов подряд до остановки, 5),
     ALI_MAX_TRIES (заходов на тендер всего, 3),
     ALI_HARD_CAP (предохранитель: максимум тендеров за один запуск, 500)
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
RETRIES = int(os.getenv("ALI_RETRIES", "5"))
GAP_MIN = float(os.getenv("ALI_GAP_MIN", "15"))
GAP_MAX = float(os.getenv("ALI_GAP_MAX", "20"))
STOP_AFTER = int(os.getenv("ALI_STOP_AFTER", "5"))
MAX_TRIES = int(os.getenv("ALI_MAX_TRIES", "3"))
HARD_CAP = int(os.getenv("ALI_HARD_CAP", "500"))

# Все живые подобранные тендеры без цены, у кого ali-попыток меньше лимита.
# Жёсткого LIMIT нет — берём пачкой до HARD_CAP (предохранитель), но реально
# проход остановит детектор квоты гораздо раньше.
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
    med = statistics.median(mins)
    moqs = [p.get("moq") for p in products if p.get("moq")]
    return {
        "price": round(med, 2),
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
    print("Alibaba-проход: в очереди %d тендеров | пауза %g-%gс наугад | стоп после %d отказов подряд"
          % (len(rows), GAP_MIN, GAP_MAX, STOP_AFTER))
    if not rows:
        cur.close()
        conn.close()
        return

    fetcher = AlibabaPriceFetcher()
    got = 0
    processed = 0
    miss_streak = 0
    stopped = False
    try:
        for i, (rid, name, spec_raw, mr_raw) in enumerate(rows, 1):
            spec = as_dict(spec_raw)
            ru = ru_source(name, spec)
            kw = en_keyword(client, ru)
            if not kw:
                cur.execute(UPDATE_SQL, (json.dumps(merge_result(mr_raw, None), ensure_ascii=False), rid))
                print("  [%d] перевод не удался | id=%s" % (i, rid))
                continue

            products, att = fetcher.search(kw, retries=RETRIES, delay=3.0)
            orient = build_orient(products) if products else None
            cur.execute(UPDATE_SQL, (json.dumps(merge_result(mr_raw, orient), ensure_ascii=False), rid))
            processed += 1

            if orient:
                got += 1
                miss_streak = 0
                shown = "med $%.2f ($%.2f-$%.2f, от %s)" % (
                    orient["price"], orient["ali_usd_low"], orient["ali_usd_high"], orient["ali_moq"])
            else:
                miss_streak += 1
                shown = "\u2014 (нет товаров) [подряд %d]" % miss_streak
            print("  [%d] %-38s | %s | id=%s" % (i, kw[:38], shown, rid))

            # ДЕТЕКТОР КВОТЫ: серия отказов подряд -> квота кончилась, выходим
            if miss_streak >= STOP_AFTER:
                stopped = True
                print("\n\u26d4 %d отказов подряд — похоже, квота Alibaba на наш IP исчерпана." % miss_streak)
                print("   Останавливаюсь (это нормально). Следующий запуск продолжит, когда квота восстановится.")
                break

            if i < len(rows):
                time.sleep(random.uniform(GAP_MIN, GAP_MAX))
    finally:
        cur.close()
        conn.close()

    tag = "ОСТАНОВЛЕН по квоте" if stopped else "очередь пройдена"
    print("\nИтог (%s): обработано %d, ориентир записан у %d." % (tag, processed, got))


if __name__ == "__main__":
    main()
