#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""goszakup_audit.py — СВЕРКА: что goszakup отдаёт как активное против нашей базы.

Листает ВСЕ страницы goszakup под нашим фильтром (ЗЦП, товары, от 150к,
status=240 «идёт приём»), собирает номера лотов и их статус со страницы, затем
сверяет с базой: каждый активный на goszakup лот — он у нас есть? живой или закрыт?

Отвечает на спор: теряем ли мы активные лоты (закрываем их рано) или нет.

ENV: DATABASE_URL, AUDIT_MAX_PAGES (по умолч. 200)
"""
import os
import time
from collections import Counter

import requests
from bs4 import BeautifulSoup
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
BASE = "https://goszakup.gov.kz"
# тот же фильтр, что у сборщика: method=3 (ЗЦП), status=240 (приём ЦП), товары, от 150к
FILTER = ("filter%5Bmethod%5D%5B0%5D=3&filter%5Bstatus%5D%5B0%5D=240"
          "&filter%5Bamount_from%5D=150000&filter%5Btrade_type%5D=g")
MAX_PAGES = int(os.getenv("AUDIT_MAX_PAGES", "200"))


def fetch_goszakup_lots():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    lots = {}            # lot_number -> status_text
    statuses = Counter()
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE}/ru/search/lots?{FILTER}&count_record=50&page={page}"
        try:
            r = s.get(url, timeout=30)
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.select("#search-result tbody tr")
            if not rows:
                print("стр %d: пусто — конец." % page)
                break
            page_count = 0
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 7:
                    continue
                ln_el = cells[0].find("strong")
                if not ln_el:
                    continue
                ln = ln_el.text.strip()
                status = " ".join(cells[6].text.split())
                lots[ln] = status
                statuses[status] += 1
                page_count += 1
            print("стр %d: +%d (всего %d)" % (page, page_count, len(lots)))
            if page_count == 0:
                break
            time.sleep(0.3)
        except Exception as e:
            print("стр %d: ошибка %s" % (page, e))
            break
    return lots, statuses


def main():
    print("Скачиваю активные лоты с goszakup (фильтр: ЗЦП, товары, от 150к, идёт приём)...\n")
    gos, statuses = fetch_goszakup_lots()
    print("\nВсего goszakup отдал лотов под фильтром: %d" % len(gos))
    print("Статусы (как goszakup их называет):")
    for st, c in statuses.most_common():
        print("   %-50s %d" % (st[:50], c))
    if not gos:
        print("\nНичего не получили — проверь доступ к goszakup.")
        return

    nums = list(gos.keys())
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        "SELECT lot_number, is_closed, deadline FROM tenders WHERE lot_number = ANY(%s)",
        (nums,),
    )
    db = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cur.close()
    conn.close()

    in_db = live = closed = missing = 0
    closed_ex, missing_ex = [], []
    for ln in nums:
        if ln in db:
            in_db += 1
            is_closed, deadline = db[ln]
            if is_closed:
                closed += 1
                if len(closed_ex) < 15:
                    closed_ex.append((ln, deadline))
            else:
                live += 1
        else:
            missing += 1
            if len(missing_ex) < 15:
                missing_ex.append(ln)

    print("\n========== ИТОГ СВЕРКИ ==========")
    print("goszakup активных лотов:                 %d" % len(gos))
    print("  из них ЕСТЬ в нашей базе:              %d" % in_db)
    print("    \u2514 живых у нас (на витрине):         %d" % live)
    print("    \u2514 ЗАКРЫТЫ у нас (а goszakup активны): %d" % closed)
    print("  НЕТ в нашей базе вообще:               %d" % missing)
    print("=================================")

    if closed:
        print("\n\u26a0\ufe0f %d лотов goszakup считает активными, а мы ЗАКРЫЛИ. Примеры (номер | наш deadline):" % closed)
        for ln, dl in closed_ex:
            print("   %s | %s" % (ln, dl))
    if missing:
        print("\n\u26a0\ufe0f %d активных лотов goszakup мы вообще НЕ собрали. Примеры:" % missing)
        for ln in missing_ex:
            print("   %s" % ln)

    print("\n========== ВЕРДИКТ ==========")
    if closed == 0 and missing == 0:
        print("\u2705 Все активные лоты goszakup есть у нас и ЖИВЫЕ. Потерь нет — система права.")
    elif closed > 100 or missing > 100:
        print("\u2757 Много активных лотов закрыто/не собрано. ТЫ ПРАВ — мы теряем лоты, надо чинить.")
    else:
        print("\u2139\ufe0f Небольшие расхождения (%d закрыто, %d не собрано) — пограничные случаи, разберём." % (closed, missing))


if __name__ == "__main__":
    main()
