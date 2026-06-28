#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_sync.py — ЕДИНЫЙ проход сверки с goszakup (заменяет ненадёжный CSV-сбор).

Каждый запуск:
  1) тянет ВСЕ активные номера лотов с goszakup (надёжная пагинация до конца);
  2) сверяет с НАШЕЙ базой (а не с CSV-файлом!) — находит НОВЫЕ номера;
  3) синхронизирует статусы: активные -> is_closed=false, пропавшие -> is_closed=true
     (источник истины — присутствие в выдаче, а не ненадёжный дедлайн);
  4) для НОВЫХ лотов качает ТЗ (PDF) и вставляет в базу со stage='collected' —
     дальше их сами подхватят воркеры (нормализация -> поиск -> витрина).

ЗАЩИТА: если выдача скачана не полностью или в ней < MIN_SAFE лотов — база НЕ
трогается. Есть --dry-run (показать, ничего не меняя). Лимит новых за прогон
MAX_NEW (чтобы первый прогон не висел вечно, если новых вдруг много).

ENV: DATABASE_URL, MAX_PAGES(300), MIN_SAFE(3000), MAX_NEW(400)
Запуск: python daily_sync.py [--dry-run]
"""
import os
import sys
import time
import asyncio
import re
from datetime import datetime, date

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from techspec_dumper import GoszakupParser  # переиспользуем сессию и скачивание ТЗ

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
BASE = "https://goszakup.gov.kz"
FILTER = ("filter%5Bmethod%5D%5B0%5D=3&filter%5Bstatus%5D%5B0%5D=240"
          "&filter%5Bamount_from%5D=150000&filter%5Btrade_type%5D=g")
MAX_PAGES = int(os.getenv("MAX_PAGES", "300"))
MIN_SAFE = int(os.getenv("MIN_SAFE", "3000"))
MAX_NEW = int(os.getenv("MAX_NEW", "400"))
DRY_RUN = "--dry-run" in sys.argv


def parse_deadline(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def parse_page_rows(html):
    """Из HTML страницы поиска -> список всех лотов (БЕЗ пропуска без цены —
    чтобы для синхронизации статусов был полный набор активных номеров)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("#search-result tbody tr")
    out = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        ln_el = cells[0].find("strong")
        if not ln_el:
            continue
        ln = ln_el.text.strip()

        ann_a = cells[1].find("a")
        ann_href = ann_a.get("href", "") if ann_a else ""
        announce_id = ann_href.split("/")[-1] if ann_href else ""

        lot_a = cells[2].find("a")
        lot_href = lot_a.get("href", "") if lot_a else ""
        lot_url = lot_href if lot_href.startswith("http") else (BASE + lot_href if lot_href else "")
        name_el = cells[2].find("strong")
        name = name_el.text.strip() if name_el else (lot_a.text.strip() if lot_a else ln)

        price = None
        pm = re.search(r"([\d\s,]+\.\d{2})", cells[4].text)
        if pm:
            try:
                price = float(pm.group(1).replace(" ", "").replace(",", ""))
            except Exception:
                price = None
        try:
            qty = int(re.sub(r"[^\d]", "", cells[3].text) or "1")
        except Exception:
            qty = 1
        if qty <= 0:
            qty = 1
        if price is not None and qty > 0:
            price = price / qty  # price_per_unit как в старом сборе

        customer = "Не указан"
        sm = cells[1].find("small")
        if sm and "Заказчик:" in sm.text:
            customer = sm.text.split("Заказчик:")[-1].strip()

        out.append({"lot_number": ln, "announce_id": announce_id, "lot_url": lot_url,
                    "name": name, "price": price, "quantity": qty, "customer": customer})
    return out


async def main():
    print("Единый проход сверки с goszakup%s" % (" [DRY-RUN]" if DRY_RUN else ""))
    parser = GoszakupParser()       # даёт self.session и get_lot_specifications/get_announce_deadline
    session = parser.session

    # ── 1) собрать все активные лоты ──
    active = {}
    completed = False
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE}/ru/search/lots?{FILTER}&count_record=50&page={page}"
        try:
            r = await asyncio.to_thread(session.get, url, timeout=40)
            if r.status_code != 200:
                print("стр %d: HTTP %s — обрыв, база НЕ тронется." % (page, r.status_code))
                completed = False
                break
            rows = parse_page_rows(r.text)
        except Exception as e:
            print("стр %d: ошибка %s — обрыв, база НЕ тронется." % (page, e))
            completed = False
            break
        if not rows:
            completed = True
            break
        for it in rows:
            active[it["lot_number"]] = it
        if page % 20 == 0:
            print("  ... стр %d, активных %d" % (page, len(active)))
        time.sleep(0.25)

    print("\nАктивных на goszakup: %d (полностью: %s)" % (len(active), "да" if completed else "НЕТ"))
    if not completed:
        print("\u26a0\ufe0f Выдача скачана не полностью — база НЕ изменена (защита).")
        return
    if len(active) < MIN_SAFE:
        print("\u26a0\ufe0f Активных всего %d (< %d) — база НЕ изменена (защита)." % (len(active), MIN_SAFE))
        return

    conn = await asyncpg.connect(DATABASE_URL)
    existing = set(r["lot_number"] for r in await conn.fetch("SELECT lot_number FROM tenders"))
    active_nums = set(active.keys())
    new_nums = active_nums - existing
    print("Уже в базе из активных: %d | НОВЫХ (нет в базе): %d" % (len(active_nums & existing), len(new_nums)))

    if DRY_RUN:
        print("\nDRY-RUN: добавил бы %d новых и синхронизировал статусы. База НЕ изменена." % len(new_nums))
        for ln in list(new_nums)[:12]:
            print("   новый: %s | %s" % (ln, active[ln]["name"][:45]))
        await conn.close()
        return

    # ── 2) синхронизация статусов (по присутствию) ──
    nums = list(active_nums)
    o = await conn.execute("UPDATE tenders SET is_closed=false WHERE is_closed=true AND lot_number = ANY($1::text[])", nums)
    c = await conn.execute("UPDATE tenders SET is_closed=true WHERE is_closed=false AND NOT (lot_number = ANY($1::text[]))", nums)
    print("Статусы: открыто %s, закрыто %s" % (o.split()[-1], c.split()[-1]))

    # ── 3) собрать НОВЫЕ лоты (ТЗ) и вставить ──
    added = 0
    today = date.today()
    todo = list(new_nums)[:MAX_NEW]
    for i, ln in enumerate(todo, 1):
        it = active[ln]
        try:
            raw = await asyncio.to_thread(parser.get_lot_specifications, it["lot_url"], it["announce_id"])
        except Exception:
            raw = ""
        try:
            dl = await asyncio.to_thread(parser.get_announce_deadline, it["announce_id"])
        except Exception:
            dl = ""
        await conn.execute(
            "INSERT INTO tenders (lot_number,name,price_per_unit,quantity,unit,customer,"
            "deadline,raw_spec,last_seen,stage,is_closed) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'collected',false) "
            "ON CONFLICT (lot_number) DO NOTHING",
            ln, it["name"], it["price"], it["quantity"], "ед.", it["customer"],
            parse_deadline(dl), (raw or None), today)
        added += 1
        if i % 25 == 0:
            print("   ... добавлено %d/%d" % (added, len(todo)))

    print("\n\u2705 Новых добавлено: %d (stage=collected -> воркеры нормализуют и ищут сами)" % added)
    if len(new_nums) > MAX_NEW:
        print("Осталось на следующий прогон: %d (лимит MAX_NEW=%d)" % (len(new_nums) - MAX_NEW, MAX_NEW))
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
