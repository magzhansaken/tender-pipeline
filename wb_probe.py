"""
wb_probe.py — какой способ реально отдаёт цену Wildberries по артикулу?
Пробует несколько endpoint'ов И двумя способами: обычный requests и curl_cffi
(маскировка под Chrome — обход блокировки, как было в твоём зипе).

    python wb_probe.py            # дефолтный артикул
    python wb_probe.py 178851338  # свой артикул (nmId из ссылки /catalog/NM/detail)

Ставит: requests, curl_cffi.
"""
import json
import sys

NM = sys.argv[1] if len(sys.argv) > 1 else "178851338"

ENDPOINTS = [
    ("v2",      f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={NM}"),
    ("v1",      f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&nm={NM}"),
    ("v2+spp",  f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}"),
    ("u-card",  f"https://u-card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={NM}"),
]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}


def find_price(text):
    try:
        d = json.loads(text)
    except Exception:
        return None
    prods = (d.get("data") or {}).get("products") or []
    if not prods:
        return None
    p = prods[0]
    for sz in p.get("sizes", []):
        pr = sz.get("price") or {}
        raw = pr.get("product") or pr.get("total") or pr.get("basic")
        if raw:
            return round(raw / 100)
    raw = p.get("salePriceU") or p.get("priceU")
    if raw:
        return round(raw / 100)
    return None


def show(tag, status, text):
    price = find_price(text)
    flag = f"ЦЕНА {price} ₽ ✅" if price else "цены нет"
    print(f"  [{tag:10}] HTTP {status} | размер {len(text):>6} | {flag}")


print(f"Артикул (nmId): {NM}\n")

print("=== 1) обычный requests ===")
import requests
for name, url in ENDPOINTS:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        show(f"req {name}", r.status_code, r.text)
    except Exception as e:
        print(f"  [req {name:6}] ошибка: {str(e)[:55]}")

print("\n=== 2) curl_cffi (маскировка под Chrome120) ===")
try:
    from curl_cffi import requests as creq
    for name, url in ENDPOINTS:
        try:
            r = creq.get(url, impersonate="chrome120", timeout=20)
            show(f"cffi {name}", r.status_code, r.text)
        except Exception as e:
            print(f"  [cffi {name:6}] ошибка: {str(e)[:55]}")
except Exception as e:
    print("  curl_cffi недоступен:", str(e)[:60])

print("\nИтог: какая строка с '✅ ЦЕНА' — тот способ и вставим в код.")
