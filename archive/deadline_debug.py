#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deadline_debug.py — смотрим, где на странице объявления goszakup настоящий
срок приёма, и что цепляет текущий парсер. Берём несколько лотов с 1-й страницы
поиска, достаём ПРАВИЛЬНУЮ ссылку объявления (как сборщик — из cells[1]),
заходим и печатаем все даты с подписями.
"""
import re
import requests
from bs4 import BeautifulSoup

BASE = "https://goszakup.gov.kz"
FILTER = ("filter%5Bmethod%5D%5B0%5D=3&filter%5Bstatus%5D%5B0%5D=240"
          "&filter%5Bamount_from%5D=150000&filter%5Btrade_type%5D=g")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?")

# регулярка текущего сборщика (та, что в get_announce_deadline)
CUR_RE = re.compile(
    r"Срок\s+окончания\s+приема\s+заявок.{0,300}?value=['\"]\s*"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}:[0-9]{2})?)",
    re.DOTALL,
)


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

    r = s.get(f"{BASE}/ru/search/lots?{FILTER}&count_record=50&page=1", timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select("#search-result tbody tr")
    print("лотов на стр.1: %d\n" % len(rows))

    picked = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        ln_el = cells[0].find("strong")
        a = cells[1].find("a")
        if not ln_el or not a:
            continue
        ln = ln_el.text.strip()
        href = a.get("href", "")
        aid = href.split("/")[-1] if href else ""
        status = " ".join(cells[6].text.split())
        picked.append((ln, href, aid, status))
        if len(picked) >= 3:
            break

    for ln, href, aid, status in picked:
        print("=" * 70)
        print("ЛОТ %s | статус: %s" % (ln, status))
        print("  announce href: %s" % href)
        print("  announce_id (последний кусок): %s" % aid)
        url = href if href.startswith("http") else BASE + href
        print("  URL объявления: %s" % url)
        try:
            rr = s.get(url, timeout=20)
            print("  HTTP: %s | размер: %d" % (rr.status_code, len(rr.text)))
            html = rr.text

            # что цепляет ТЕКУЩИЙ парсер
            m = CUR_RE.search(html)
            print("  >>> текущий парсер взял бы: %s" % (m.group(1) if m else "НИЧЕГО (пусто)"))

            # все даты value="..." с контекстом (как в input-полях формы)
            print("  --- даты в value=\"...\" (с подписью слева) ---")
            seen = 0
            for mm in re.finditer(
                r"([\s\S]{0,120})value=['\"]\s*"
                r"(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)['\"]", html):
                ctx = " ".join(re.sub(r"<[^>]+>", " ", mm.group(1)).split())[-60:]
                print("      %s   <= …%s" % (mm.group(2), ctx))
                seen += 1
                if seen >= 12:
                    break
            if seen == 0:
                print("      (в value= дат не найдено)")

            # видимый текст: строки про срок/приём + соседние строки с датой
            soup2 = BeautifulSoup(html, "html.parser")
            lines = [x.strip() for x in soup2.get_text("\n").split("\n") if x.strip()]
            print("  --- видимый текст: подписи про срок/приём и даты рядом ---")
            shown = 0
            for i, t in enumerate(lines):
                low = t.lower()
                if ("срок" in low and ("прием" in low or "оконч" in low)) or DATE.search(t):
                    nbrs = " | ".join(lines[i:i + 2])[:110]
                    print("      %s" % nbrs)
                    shown += 1
                    if shown >= 14:
                        break
            if shown == 0:
                print("      (подписей/дат в тексте не найдено)")
        except Exception as e:
            print("  ошибка: %s" % e)
        print()


if __name__ == "__main__":
    main()
