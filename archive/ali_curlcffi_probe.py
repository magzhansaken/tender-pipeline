#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ali_curlcffi_probe.py — пробуем AliExpress через curl_cffi (TLS-импersonation):
несколько отпечатков Chrome/Safari x несколько адресов x куки региона.
Это третий путь помимо requests и Playwright. Печатает по каждому варианту:
статус, финальный URL, размер, валюту, цены, островок данных, признак капчи.

    python ali_curlcffi_probe.py "drill bosch"
"""
import re
import sys

try:
    from curl_cffi import requests as creq
except Exception:
    creq = None

Q = sys.argv[1] if len(sys.argv) > 1 else "drill bosch"
QP = Q.replace(" ", "+")
QD = Q.replace(" ", "-")

CUR = {"₸": "\u20b8", "₽": "\u20bd", "$": "$", "¥": "\u00a5", "€": "\u20ac"}
BLOCK = ("captcha", "punish", "slider", "verify", "are you a human", "robot check",
         "доступ ограничен", "interception", "x5referer")


def analyze(text):
    cur = {n: text.count(s) for n, s in CUR.items() if text.count(s) > 0}
    prices = re.findall(r"[\u20b8\u20bd\u00a5\u20ac]\s*(\d[\d.,\u00a0\u2009]{1,})", text)
    usd = re.findall(r"(?:US\s*\$|\$)\s*(\d[\d.,]{1,})", text)
    island = [k for k in ("runParams", "_init_data_", '"priceModule"', "window.runParams") if k in text]
    blk = [w for w in BLOCK if w in text.lower()]
    return cur, [p.strip()[:10] for p in prices[:5]], usd[:5], island, blk


RU_COOKIE = {"aep_usuc_f": "site=rus&c_tp=RUB&region=RU&b_locale=ru_RU&x_alimid=0"}

# (метка, отпечаток, url, cookies)
CASES = [
    ("chrome124 .com/w/",      "chrome124",   f"https://www.aliexpress.com/w/wholesale-{QD}.html", None),
    ("chrome124 .ru",          "chrome124",   f"https://aliexpress.ru/wholesale?SearchText={QP}", None),
    ("chrome124 .com +RU cook","chrome124",   f"https://www.aliexpress.com/w/wholesale-{QD}.html", RU_COOKIE),
    ("safari17 .com/w/",       "safari17_0",  f"https://www.aliexpress.com/w/wholesale-{QD}.html", None),
    ("chrome110 .com search",  "chrome110",   f"https://www.aliexpress.com/wholesale?SearchText={QP}", None),
]


def main():
    if creq is None:
        print("curl_cffi не установлен (pip install curl_cffi)")
        return
    print("Запрос: %r\n" % Q)
    for label, imp, url, cookies in CASES:
        try:
            r = creq.get(url, impersonate=imp, timeout=30, allow_redirects=True,
                         cookies=cookies or {})
            cur, prices, usd, island, blk = analyze(r.text)
            ok = len(r.text) > 120000 and (prices or usd or island) and not blk
            verdict = "✅ ПОХОЖЕ ПРОШЛИ" if ok else ("⚠️ капча/блок" if blk else "— пусто/мало")
            print("[%-24s] HTTP %s | %7db | %s" % (label, r.status_code, len(r.text), verdict))
            print("      финал: %s" % r.url[:62])
            print("      валюты:%s цены:%s $:%s островок:%s блок:%s"
                  % (cur or "—", prices or "—", usd or "—", island or "нет", blk or "нет"))
        except Exception as e:
            print("[%-24s] ошибка: %s" % (label, str(e)[:60]))
        print()
    print("Если где-то ✅ и островок/цены без капчи — есть рабочий путь, копаем туда.")
    print("Если везде капча/редирект/пусто — IP в стене, curl_cffi не помог.")


if __name__ == "__main__":
    main()
