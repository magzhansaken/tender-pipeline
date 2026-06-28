#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вскрываем структуру Alibaba _PAGE_DATA_: пробуем разные загрузки, и ищем
товары ВЕЗДЕ в JSON по ценовым ключам (структура могла поменяться).

    python alibaba_struct.py "drill bosch"
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
PRICE_KEYS = ("lowerPrice", "upperPrice", "tradePrice", "formatPrice", "promotionPrice",
              "priceModule", "offerId", "productId", "p4pId")


def extract(html):
    m = re.search(r"_PAGE_DATA_\s*=\s*\{", html)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    for i in range(start, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except Exception:
                    return None
    return None


def find_product_lists(o, path, out, depth=0):
    if depth > 12:
        return
    if isinstance(o, dict):
        for k, v in o.items():
            find_product_lists(v, path + "." + str(k), out, depth + 1)
    elif isinstance(o, list):
        if o and isinstance(o[0], dict):
            keyset = set()
            for el in o[:5]:
                if isinstance(el, dict):
                    keyset |= set(el.keys())
                    keyset |= set((el.get("offer") or {}).keys()) if isinstance(el.get("offer"), dict) else set()
            hit = [pk for pk in PRICE_KEYS if pk in keyset]
            if hit:
                out.append((path, len(o), hit[:4], sorted(keyset)[:10]))
        for i, v in enumerate(o[:5]):
            find_product_lists(v, path + "[" + str(i) + "]", out, depth + 1)


def main():
    q = sys.argv[1] if len(sys.argv) > 1 else "drill bosch"
    url = "https://www.alibaba.com/showroom/%s.html" % q.strip().replace(" ", "-")
    print("URL:", url, "\n")

    methods = []
    if requests is not None:
        methods.append(("requests", lambda: requests.get(url, headers={"User-Agent": UA}, timeout=30).text))
    if creq is not None:
        for imp in ("chrome116", "chrome110", "safari17_0", "chrome120"):
            methods.append(("curl_cffi:" + imp,
                            (lambda im: (lambda: creq.get(url, impersonate=im, timeout=30).text))(imp)))

    best = None
    for name, fn in methods:
        try:
            html = fn()
            has = "_PAGE_DATA_" in html
            pd = extract(html) if has else None
            n_items = 0
            if pd:
                lists = []
                find_product_lists(pd, "$", lists)
                n_items = sum(n for _, n, _, _ in lists)
            print("[%-18s] %7db | _PAGE_DATA_=%s | товаров найдено=%d"
                  % (name, len(html), has, n_items))
            if pd and n_items and best is None:
                best = (name, pd)
        except Exception as e:
            print("[%-18s] ошибка %s" % (name, str(e)[:40]))

    if not best:
        print("\nНи один способ не дал товаров. Сервер получает страницу-пустышку без данных.")
        return

    name, pd = best
    print("\n=== СТРУКТУРА (способ %s) ===" % name)
    print("верхние ключи _PAGE_DATA_:", list(pd.keys())[:12])
    lists = []
    find_product_lists(pd, "$", lists)
    print("\nсписки товаров найдены тут:")
    for path, n, hit, keys in lists[:6]:
        print("  путь %-45s товаров=%-4d цен.ключи=%s" % (path[:45], n, hit))
        print("       ключи товара: %s" % keys)


if __name__ == "__main__":
    main()
