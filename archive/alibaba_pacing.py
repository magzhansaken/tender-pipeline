#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alibaba_pacing.py — ЗАМЕР: при какой паузе между запросами Alibaba ещё
стабильно отдаёт товары. Берёт РАЗНЫЕ по типу тендеры, переводит ключ
Олламой, и гоняет тремя режимами пауз (по умолчанию 30с, 15с, 8с),
считая процент захвата в каждом. ТОЛЬКО ЧИТАЕТ базу.

Чтобы сравнение было честным — для каждого режима берём СВОЙ набор тендеров
(одинакового размера), иначе «разогретый» бюджет от первого режима исказил бы
второй. Между режимами — большая пауза «остывания».

ENV: DATABASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL, PER (тендеров на режим, 5),
     GAPS (список пауз через запятую, "30,15,8"), COOLDOWN (между режимами, 60),
     RETRIES (попыток поиска, 4)

    python alibaba_pacing.py
"""
import os
import json
import time

import psycopg2
from ollama import Client

from alibaba_price import AlibabaPriceFetcher

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
PER = int(os.getenv("PER", "5"))
GAPS = [int(x) for x in os.getenv("GAPS", "30,15,8").split(",")]
COOLDOWN = int(os.getenv("COOLDOWN", "60"))
RETRIES = int(os.getenv("RETRIES", "4"))

# Берём РАЗНЫЕ по типу товара тендеры: по одному на product_type (DISTINCT ON),
# чтобы не мерить на однотипных. Нужно PER*len(GAPS) штук.
SELECT_SQL = """
SELECT DISTINCT ON (structured_spec->>'product_type')
       id, name, structured_spec
FROM tenders
WHERE structured_spec IS NOT NULL
  AND match_status IN ('FOUND_EXACT','FOUND_PARTIAL')
  AND is_closed = false
  AND COALESCE(structured_spec->>'product_type','') <> ''
ORDER BY structured_spec->>'product_type', collected_at DESC
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


def main():
    if not OLLAMA_API_KEY:
        print("Нет OLLAMA_API_KEY")
        return
    client = Client(host="https://ollama.com", headers={"Authorization": "Bearer " + OLLAMA_API_KEY})

    need = PER * len(GAPS)
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(SELECT_SQL, (need,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if len(rows) < need:
        print("Внимание: разных типов товара нашлось %d, нужно %d — режимы будут меньше." % (len(rows), need))

    # заранее переводим ключи (перевод не влияет на бюджет Alibaba)
    items = []
    for rid, name, spec_raw in rows:
        spec = as_dict(spec_raw)
        ru = ru_source(name, spec)
        kw = en_keyword(client, ru)
        items.append((kw, ru))
    items = [it for it in items if it[0]]

    print("=" * 70)
    print("ЗАМЕР ПАУЗ Alibaba | по %d разных товаров на режим | ретраев %d" % (PER, RETRIES))
    print("=" * 70)

    fetcher = AlibabaPriceFetcher()
    idx = 0
    summary = []
    for gi, gap in enumerate(GAPS):
        batch = items[idx:idx + PER]
        idx += PER
        if not batch:
            break
        hit = 0
        print("\n----- ПАУЗА %dс -----" % gap)
        for j, (kw, ru) in enumerate(batch, 1):
            rows_p, att = fetcher.search(kw, retries=RETRIES, delay=3.0)
            ok = len(rows_p) > 0
            if ok:
                hit += 1
            print("  %-38s -> %s (товаров %d, попытка %d)"
                  % (kw[:38], "OK" if ok else "—", len(rows_p), att))
            if j < len(batch):
                time.sleep(gap)
        rate = hit * 100 // len(batch)
        summary.append((gap, hit, len(batch), rate))
        print("  ИТОГ паузы %dс: поймал %d из %d (%d%%)" % (gap, hit, len(batch), rate))
        if gi < len(GAPS) - 1:
            print("  ... остывание %dс перед следующим режимом ..." % COOLDOWN)
            time.sleep(COOLDOWN)

    print("\n" + "=" * 70)
    print("СВОДКА: где Alibaba ещё стабилен")
    print("=" * 70)
    for gap, hit, tot, rate in summary:
        bar = "#" * (rate // 10)
        print("  пауза %3dс : %2d/%2d  %3d%%  %s" % (gap, hit, tot, rate, bar))
    print("\nВыбираем самую КОРОТКУЮ паузу, где захват ещё высокий.")


if __name__ == "__main__":
    main()
