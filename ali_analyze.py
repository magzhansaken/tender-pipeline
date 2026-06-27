#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ali_analyze.py — вскрываем структуру AliExpress.com: тянем страницу
рабочим рецептом curl_cffi (safari17/chrome110), достаём островок _init_data_,
парсим JSON и ищем, ГДЕ лежат цены (путь + валюта). Это разведка, не парсер.

    python ali_analyze.py "drill bosch"
"""
import re
import sys
import json

try:
    from curl_cffi import requests as creq
except Exception:
    creq = None

Q = sys.argv[1] if len(sys.argv) > 1 else "drill bosch"
QD = Q.replace(" ", "-")
URL = f"https://www.aliexpress.com/w/wholesale-{QD}.html"
IMPS = ["safari17_0", "chrome110", "chrome116"]   # отпечатки, что проходили капчу


def get_html():
    for imp in IMPS:
        try:
            r = creq.get(URL, impersonate=imp, timeout=30, allow_redirects=True)
            if len(r.text) > 120000 and "captcha" not in r.text.lower():
                return r.text, imp
        except Exception:
            pass
    return None, None


def extract_init_data(html):
    """Достаём JSON из window._init_data_ = {...};"""
    for pat in (r"_init_data_\s*=\s*({.*?})\s*</script>",
                r"_init_data_\s*=\s*({.*?});\s*window",
                r"window\.runParams\s*=\s*({.*?})\s*</script>"):
        m = re.search(pat, html, re.S)
        if m:
            frag = m.group(1)
            try:
                return json.loads(frag)
            except Exception:
                # иногда вложено как {data: {...}} с хвостом — пробуем обрезать по балансу скобок
                depth = 0
                for i, ch in enumerate(frag):
                    depth += (ch == "{") - (ch == "}")
                    if depth == 0:
                        try:
                            return json.loads(frag[:i + 1])
                        except Exception:
                            break
    return None


# ключи, похожие на цену
PRICE_KEYS = ("price", "salePrice", "minPrice", "maxPrice", "formatedPrice",
              "formattedPrice", "minActivityAmount", "value", "amount")


def walk_prices(o, path, out, depth=0):
    if depth > 14:
        return
    if isinstance(o, dict):
        for k, v in o.items():
            kl = str(k).lower()
            if any(pk.lower() in kl for pk in PRICE_KEYS):
                if isinstance(v, (str, int, float)):
                    s = str(v)
                    if re.search(r"\d", s) and len(s) < 40:
                        out.append((path + "." + str(k), s))
                elif isinstance(v, dict):
                    # цена часто объект {value:.., currency:..}
                    inner = {ik: v.get(ik) for ik in ("value", "currency", "formatedAmount",
                             "formattedAmount", "amount", "currencyCode") if ik in v}
                    if inner:
                        out.append((path + "." + str(k), json.dumps(inner, ensure_ascii=False)[:60]))
            walk_prices(v, path + "." + str(k), out, depth + 1)
    elif isinstance(o, list):
        for i, v in enumerate(o[:30]):
            walk_prices(v, path + "[" + str(i) + "]", out, depth + 1)


def _selftest():
    sample = {"data": {"root": {"fields": {"mods": {"itemList": {"content": [
        {"productId": 100, "prices": {"salePrice": {"formattedPrice": "$12.34",
            "minPrice": 12.34, "currencyCode": "USD"}}},
        {"productId": 200, "prices": {"salePrice": {"formattedPrice": "$5.00",
            "minPrice": 5.0, "currencyCode": "USD"}}}]}}}}}}
    out = []
    walk_prices(sample, "$", out)
    got = [s for _, s in out]
    assert any("12.34" in s for s in got), got
    print("selftest OK: нашёл цены в учебной структуре (%d совпадений)" % len(out))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
        return
    if creq is None:
        print("curl_cffi не установлен")
        return
    html, imp = get_html()
    print("================ РАЗВЕДКА ALIEXPRESS _init_data_ ================")
    if not html:
        print("Не удалось получить выдачу без капчи ни одним отпечатком.")
        return
    print("Рецепт сработал:", imp, "| размер HTML:", len(html))
    data = extract_init_data(html)
    if not data:
        print("Островок _init_data_ найден в тексте, но JSON не распарсился.")
        print("Сырой фрагмент (первые 400 симв.):")
        m = re.search(r"_init_data_\s*=\s*({.*?)</script>", html, re.S)
        print((m.group(1)[:400] if m else "не найден"))
        return
    print("JSON _init_data_ распарсен. Верхние ключи:", list(data.keys())[:8])
    out = []
    walk_prices(data, "$", out)
    print("\nНайдено мест с ценой:", len(out), "(первые 25):")
    seen = set()
    for path, val in out:
        key = (path.split("[")[0], val)   # схлопываем индексы списков
        if key in seen:
            continue
        seen.add(key)
        print("  %-70s = %s" % (path[:70], val))
        if len(seen) >= 25:
            break


if __name__ == "__main__":
    main()
