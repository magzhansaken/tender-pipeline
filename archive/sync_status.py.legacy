#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_status.py — синхронизирует is_closed с РЕАЛЬНОЙ активной выдачей goszakup.

Почему так, а не по дате: у переобъявленных лотов (ЗЦП2/ЗЦП3) на странице
объявления лежит срок СТАРОГО этапа, поэтому закрытие «по deadline» ошибочно
прячет живые лоты (нашли потерю ~1839). Источник истины — сам goszakup:
  • лот ЕСТЬ в активной выдаче (статус=240) -> живой (is_closed=false);
  • лота НЕТ -> приём кончился (is_closed=true).

ЗАЩИТА ОТ СБОЯ: база меняется только если выдача скачана ПОЛНОСТЬЮ (без обрыва)
и в ней >= SYNC_MIN_SAFE лотов. Иначе база не трогается (сетевой сбой не должен
закрыть всё). Есть режим --dry-run (показать, ничего не меняя).

ENV: DATABASE_URL, SYNC_MAX_PAGES (300), SYNC_MIN_SAFE (3000), SYNC_DRY_RUN (0/1)
Запуск:  python sync_status.py            # реальный прогон
         python sync_status.py --dry-run  # только показать, что изменится
"""
import os
import sys
import time

import requests
from bs4 import BeautifulSoup
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
BASE = "https://goszakup.gov.kz"
# тот же фильтр, что у сборщика: ЗЦП(3), приём ЦП(240), товары(g), от 150к
FILTER = ("filter%5Bmethod%5D%5B0%5D=3&filter%5Bstatus%5D%5B0%5D=240"
          "&filter%5Bamount_from%5D=150000&filter%5Btrade_type%5D=g")
MAX_PAGES = int(os.getenv("SYNC_MAX_PAGES", "300"))
MIN_SAFE = int(os.getenv("SYNC_MIN_SAFE", "3000"))
DRY_RUN = os.getenv("SYNC_DRY_RUN", "0") == "1" or "--dry-run" in sys.argv


def fetch_active():
    """Возвращает (set номеров лотов, completed_bool).
    completed=True только если дошли до пустой страницы (всё скачали без обрыва)."""
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    active = set()
    completed = False
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE}/ru/search/lots?{FILTER}&count_record=50&page={page}"
        try:
            r = s.get(url, timeout=30)
            if r.status_code != 200:
                print("стр %d: HTTP %s — обрыв, прекращаю (база НЕ тронется)." % (page, r.status_code))
                return active, False
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.select("#search-result tbody tr")
            if not rows:
                completed = True  # дошли до конца чисто
                break
            page_count = 0
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 7:
                    continue
                ln_el = cells[0].find("strong")
                if not ln_el:
                    continue
                active.add(ln_el.text.strip())
                page_count += 1
            if page % 20 == 0:
                print("  ... стр %d, собрано %d" % (page, len(active)))
            if page_count == 0:
                completed = True
                break
            time.sleep(0.25)
        except Exception as e:
            print("стр %d: ошибка %s — обрыв, прекращаю (база НЕ тронется)." % (page, e))
            return active, False
    return active, completed


def main():
    print("Синхронизация статусов с goszakup%s\n" % (" [DRY-RUN]" if DRY_RUN else ""))
    print("Скачиваю активную выдачу goszakup (ЗЦП, товары, от 150к, идёт приём)...")
    active, completed = fetch_active()
    print("\nСобрано активных лотов: %d (полностью: %s)" % (len(active), "да" if completed else "НЕТ"))

    # ── защита ──
    if not completed:
        print("\n\u26a0\ufe0f Выдача скачана НЕ полностью (обрыв/ошибка). База НЕ изменена — для безопасности.")
        return
    if len(active) < MIN_SAFE:
        print("\n\u26a0\ufe0f Активных лотов всего %d (порог %d). Подозрительно мало — "
              "база НЕ изменена (защита от сбоя сети)." % (len(active), MIN_SAFE))
        return

    nums = list(active)
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT count(*) FILTER (WHERE is_closed=false), "
                "count(*) FILTER (WHERE is_closed=true) FROM tenders")
    live_before, closed_before = cur.fetchone()

    cur.execute("SELECT count(*) FROM tenders WHERE is_closed=true AND lot_number = ANY(%s)", (nums,))
    to_open = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM tenders WHERE is_closed=false AND NOT (lot_number = ANY(%s))", (nums,))
    to_close = cur.fetchone()[0]

    print("\nСейчас в базе:  живых %d, закрытых %d" % (live_before, closed_before))
    print("Будет ОТКРЫТО (вернётся на витрину): %d" % to_open)
    print("Будет ЗАКРЫТО (приём кончился):      %d" % to_close)

    if DRY_RUN:
        print("\nDRY-RUN: база НЕ изменена. Убери --dry-run для реального прогона.")
        cur.close(); conn.close()
        return

    cur.execute("UPDATE tenders SET is_closed=false WHERE is_closed=true AND lot_number = ANY(%s)", (nums,))
    opened = cur.rowcount
    cur.execute("UPDATE tenders SET is_closed=true WHERE is_closed=false AND NOT (lot_number = ANY(%s))", (nums,))
    closed = cur.rowcount
    conn.commit()

    cur.execute("SELECT count(*) FILTER (WHERE is_closed=false), "
                "count(*) FILTER (WHERE is_closed=true) FROM tenders")
    live_after, closed_after = cur.fetchone()
    cur.close(); conn.close()

    print("\n\u2705 Готово. Открыто: %d, закрыто: %d" % (opened, closed))
    print("Живых стало: %d (было %d), закрытых: %d (было %d)"
          % (live_after, live_before, closed_after, closed_before))
    print("Дальше публикация (publish) вынесет открытые лоты на витрину.")


if __name__ == "__main__":
    main()
