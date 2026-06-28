#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deadline_by_lot.py — берём номера лотов из активной выдачи goszakup, заходим
на СТРАНИЦУ ЛОТА (не объявления) и достаём реальный срок приёма. Рядом —
что в нашей базе и что на странице объявления (которую берёт сборщик).
Так увидим, где настоящий дедлайн и врёт ли тот, что у нас.
"""
import os
import re
import time

import requests
from bs4 import BeautifulSoup
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
BASE = "https://goszakup.gov.kz"
FILTER = ("filter%5Bmethod%5D%5B0%5D=3&filter%5Bstatus%5D%5B0%5D=240"
          "&filter%5Bamount_from%5D=150000&filter%5Btrade_type%5D=g")
N = int(os.getenv("N", "10"))
DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?")
ANN_RE = re.compile(
    r"Срок\s+окончания\s+приема\s+заявок.{0,300}?value=['\"]\s*"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}:[0-9]{2})?)",
    re.DOTALL)


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

    # собрать N лотов с поиска: номер + ссылка на лот + ссылка на объявление
    picked = []
    page = 1
    while len(picked) < N and page <= 5:
        r = s.get(f"{BASE}/ru/search/lots?{FILTER}&count_record=50&page={page}", timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("#search-result tbody tr")
        if not rows:
            break
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 7:
                continue
            ln_el = cells[0].find("strong")
            ann_a = cells[1].find("a")
            lot_a = cells[2].find("a")
            if not ln_el:
                continue
            ln = ln_el.text.strip()
            ann_href = ann_a.get("href", "") if ann_a else ""
            lot_href = lot_a.get("href", "") if lot_a else ""
            status = " ".join(cells[6].text.split())
            picked.append((ln, lot_href, ann_href, status))
            if len(picked) >= N:
                break
        page += 1

    # из базы — наши дедлайны/статусы по этим номерам
    nums = [p[0] for p in picked]
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT lot_number, deadline, is_closed, match_status FROM tenders WHERE lot_number = ANY(%s)", (nums,))
    db = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
    cur.close(); conn.close()

    for ln, lot_href, ann_href, status in picked:
        print("=" * 72)
        print("ЛОТ %s | goszakup: %s" % (ln, status))
        d = db.get(ln)
        if d:
            print("  НАША база: deadline=%s | is_closed=%s | поиск=%s" % (d[0], d[1], d[2]))
        else:
            print("  НАША база: (нет в базе)")

        # страница ОБЪЯВЛЕНИЯ (что берёт сборщик)
        ann_url = (ann_href if ann_href.startswith("http") else BASE + ann_href) if ann_href else ""
        if ann_url:
            try:
                ar = s.get(ann_url, timeout=20)
                m = ANN_RE.search(ar.text)
                print("  ОБЪЯВЛЕНИЕ (берёт сборщик): %s" % (m.group(1) if m else "пусто"))
            except Exception as e:
                print("  ОБЪЯВЛЕНИЕ: ошибка %s" % e)

        # страница ЛОТА — ищем настоящий срок
        lot_url = (lot_href if lot_href.startswith("http") else BASE + lot_href) if lot_href else ""
        print("  СТРАНИЦА ЛОТА: %s" % lot_url)
        if lot_url:
            try:
                lr = s.get(lot_url, timeout=20)
                lsoup = BeautifulSoup(lr.text, "html.parser")
                lines = [x.strip() for x in lsoup.get_text("\n").split("\n") if x.strip()]
                print("    HTTP %s | даты и подписи про срок/приём на странице лота:" % lr.status_code)
                shown = 0
                for i, t in enumerate(lines):
                    low = t.lower()
                    if ("срок" in low or "прием" in low or "оконча" in low) or DATE.search(t):
                        nxt = lines[i + 1] if i + 1 < len(lines) else ""
                        print("       %s | %s" % (t[:55], nxt[:35]))
                        shown += 1
                        if shown >= 18:
                            break
                if shown == 0:
                    print("       (на странице лота дат/подписей не найдено)")
            except Exception as e:
                print("    ошибка: %s" % e)
        time.sleep(0.3)
        print()


if __name__ == "__main__":
    main()
