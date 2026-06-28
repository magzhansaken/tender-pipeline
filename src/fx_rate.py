#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fx_rate.py — живой официальный курс Нацбанка РК (USD/RUB -> тенге).

Источник: https://nationalbank.kz/rss/rates_all.xml (без ключа, формат
<item><title>USD</title><description>478.55</description>...</item>).
Курс меняется раз в сутки — кешируем в файл на 24ч. Если Нацбанк недоступен —
берём последнее из кеша; нет кеша — безопасный дефолт. НИКОГДА не падает.

get_rate("USD") -> float  (сколько тенге за 1 доллар)
get_rate("RUB") -> float  (сколько тенге за 1 рубль)

    python fx_rate.py            # показать текущие курсы
    python fx_rate.py --selftest # проверить парсер на образце
"""
import os
import re
import json
import time
import urllib.request

URL = "https://nationalbank.kz/rss/rates_all.xml"
CACHE = os.getenv("FX_CACHE", "/tmp/fx_rates.json")
TTL = int(os.getenv("FX_TTL", "86400"))  # сутки
DEFAULTS = {"USD": 478.0, "RUB": 6.0, "EUR": 530.0, "CNY": 66.0}


def parse_xml(xml):
    """Из XML Нацбанка -> {'USD': 478.55, 'RUB': 6.01, ...}."""
    rates = {}
    # каждая валюта — блок <item> с <title>КОД</title> и <description>ЧИСЛО</description>
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        block = m.group(1)
        t = re.search(r"<title>\s*([A-Z]{3})\s*</title>", block)
        d = re.search(r"<description>\s*([\d.,]+)\s*</description>", block)
        if t and d:
            try:
                rates[t.group(1)] = float(d.group(1).replace(",", "."))
            except Exception:
                pass
    return rates


def _load_cache():
    try:
        with open(CACHE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(rates):
    try:
        with open(CACHE, "w") as f:
            json.dump({"ts": time.time(), "rates": rates}, f)
    except Exception:
        pass


def refresh(force=False):
    """Вернуть свежие курсы: из кеша (если моложе суток) или с сайта Нацбанка."""
    cached = _load_cache()
    if cached and not force and (time.time() - cached.get("ts", 0) < TTL):
        return cached.get("rates", {}), "cache"
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            xml = resp.read().decode("utf-8", "replace")
        rates = parse_xml(xml)
        if rates.get("USD"):  # успех только если хотя бы доллар распарсился
            _save_cache(rates)
            return rates, "nbk"
    except Exception:
        pass
    # сайт не дал — откатываемся на кеш (даже устаревший), потом на дефолт
    if cached and cached.get("rates"):
        return cached["rates"], "stale-cache"
    return dict(DEFAULTS), "default"


def get_rate(code):
    code = code.upper()
    rates, _src = refresh()
    val = rates.get(code)
    if val and val > 0:
        return float(val)
    return float(DEFAULTS.get(code, 1.0))


def _selftest():
    sample = """<rss><channel>
      <title>Official exchange rates</title>
      <item><title>USD</title><description>478.55</description></item>
      <item><title>RUB</title><description>6.01</description></item>
      <item><title>EUR</title><description>529.40</description></item>
    </channel></rss>"""
    r = parse_xml(sample)
    assert r.get("USD") == 478.55 and r.get("RUB") == 6.01 and r.get("EUR") == 529.40, r
    print("selftest OK: распарсил USD=%.2f RUB=%.2f EUR=%.2f" % (r["USD"], r["RUB"], r["EUR"]))


def main():
    rates, src = refresh(force=True)
    print("Источник:", src)
    for c in ("USD", "RUB", "EUR", "CNY"):
        print("  1 %s = %s тенге" % (c, rates.get(c, DEFAULTS.get(c))))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
    else:
        main()
