#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""goszakup_cols.py — диагностика: какие колонки реально в таблице лотов goszakup.
Качает одну страницу как сборщик и печатает заголовки колонок + содержимое
каждой ячейки первых строк. По этому поймём, где количество, цена, единица,
способ закупки — и почему в unit попадает «Запрос ценовых предложений».

    python goszakup_cols.py
"""
import requests
from bs4 import BeautifulSoup

URL = ("https://goszakup.gov.kz/ru/search/lots?"
       "filter%5Bmethod%5D%5B0%5D=3&filter%5Bstatus%5D%5B0%5D=240"
       "&filter%5Bamount_from%5D=150000&filter%5Btrade_type%5D=g"
       "&count_record=50&page=1")


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    r = s.get(URL, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    print("HTTP %s | размер %d" % (r.status_code, len(r.text)))

    # 1) заголовки колонок
    heads = soup.select("#search-result thead th")
    print("\n=== ЗАГОЛОВКИ КОЛОНОК (%d) ===" % len(heads))
    for i, h in enumerate(heads):
        print("  th[%d] = %r" % (i, " ".join(h.text.split())[:45]))

    # 2) содержимое ячеек первых строк
    rows = soup.select("#search-result tbody tr")
    print("\n=== строк в теле таблицы: %d ===" % len(rows))
    shown = 0
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 5:
            continue
        print("\n--- строка (ячеек %d) ---" % len(cells))
        for i, c in enumerate(cells):
            print("  cells[%d] = %r" % (i, " ".join(c.text.split())[:55]))
        shown += 1
        if shown >= 3:
            break

    if shown == 0:
        print("Не нашёл строк (структура изменилась или страница пустая).")


if __name__ == "__main__":
    main()
