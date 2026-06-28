#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alibaba_check.py — проверка Alibaba Showroom С СЕРВЕРА: тянет страницу,
достаёт window._PAGE_DATA_ и показывает товары (название, цена USD, MOQ, страна).
Логика извлечения 1:1 из твоего рабочего парсера.

    python alibaba_check.py "drill bosch"
    python alibaba_check.py --selftest
"""
import re
import sys
import json

try:
    import requests
except Exception:
    requests = None
try:
    from curl_cffi import requests as creq
except Exception:
    creq = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def fetch(query):
    qd = query.strip().replace(" ", "-")
    url = "https://www.alibaba.com/showroom/%s.html" % qd
    # 1) обычный requests
    if requests is not None:
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=30)
            if "_PAGE_DATA_" in r.text:
                return r.text, "requests", url
        except Exception:
            pass
    # 2) curl_cffi разными отпечатками
    if creq is not None:
        for imp in ("chrome116", "chrome110", "safari17_0"):
            try:
                r = creq.get(url, impersonate=imp, timeout=30)
                if "_PAGE_DATA_" in r.text:
                    return r.text, "curl_cffi:" + imp, url
            except Exception:
                pass
    return None, None, url


def extract_page_data(html):
    """window._PAGE_DATA_ = {...}; — берём по балансу скобок (надёжнее regex)."""
    m = re.search(r"_PAGE_DATA_\s*=\s*\{", html)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    for i in range(start, len(html)):
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except Exception:
                    return None
    return None


def parse_items(page_data):
    out = []
    items = (page_data or {}).get("offerResultData", {}).get("itemInfoList", [])
    for it in items:
        offer = it.get("offer", {}) or {}
        info = offer.get("information", {}) or {}
        title = info.get("enPureTitle") or (offer.get("image", {}) or {}).get("alt") or info.get("title")
        low = offer.get("lowerPrice", "")
        up = offer.get("upperPrice", "")
        if low and up:
            price = "US %s-%s" % (low, up)
        elif low:
            price = "US %s" % low
        else:
            price = (offer.get("tradePrice", {}) or {}).get("price", "") or "—"
        moq = (offer.get("tradePrice", {}) or {}).get("minOrder", "")
        country = (offer.get("company", {}) or {}).get("expCountry", "")
        if title:
            out.append((title[:42], price, moq, country))
    return out


def _selftest():
    sample = {"offerResultData": {"itemInfoList": [
        {"offer": {"id": "1601", "information": {"enPureTitle": "Desktop PC All in One 24 inch"},
                   "lowerPrice": "$221.00", "upperPrice": "$235.00",
                   "tradePrice": {"minOrder": "1 piece"}, "company": {"expCountry": "South Korea"}}},
        {"offer": {"id": "1602", "information": {"enPureTitle": "Cordless Drill 21V"},
                   "lowerPrice": "$12.50", "upperPrice": "$18.00",
                   "tradePrice": {"minOrder": "100 pieces"}, "company": {"expCountry": "China"}}},
    ]}}
    rows = parse_items(sample)
    assert len(rows) == 2 and rows[0][1] == "US $221.00-$235.00", rows
    assert rows[1][2] == "100 pieces"
    print("selftest OK: разбор _PAGE_DATA_ верный (%d товара, цены/MOQ на месте)" % len(rows))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
        return
    q = sys.argv[1] if len(sys.argv) > 1 else "drill bosch"
    html, how, url = fetch(q)
    print("================ ALIBABA SHOWROOM (с сервера) ================")
    print("URL:", url)
    if not html:
        print("Страница без _PAGE_DATA_ не пришла (бан/заглушка). Способы: requests + curl_cffi — все мимо.")
        return
    print("Получили через:", how, "| размер:", len(html))
    pd = extract_page_data(html)
    if not pd:
        print("_PAGE_DATA_ есть в тексте, но JSON не распарсился — пришлю фрагмент.")
        m = re.search(r"_PAGE_DATA_\s*=\s*(\{.{0,300})", html, re.S)
        print(m.group(1) if m else "")
        return
    rows = parse_items(pd)
    print("Товаров с ценами:", len(rows), "\n")
    for title, price, moq, country in rows[:12]:
        print("  %-42s | %-22s | MOQ: %-12s | %s" % (title, price, moq, country))
    print("\nВалюта — доллары, цены — диапазон, MOQ — минимальный заказ. Это опт.")


if __name__ == "__main__":
    main()
