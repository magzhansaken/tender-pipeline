"""
wb_probe.py — читаем ТЕЛО ответа WB через curl_cffi (он пробивает),
чтобы понять причину 403 и найти рабочий endpoint.
    python wb_probe.py 178851338
"""
import sys

NM = sys.argv[1] if len(sys.argv) > 1 else "178851338"

URLS = [
    ("bare",       f"https://card.wb.ru/cards/detail?nm={NM}"),
    ("v2-bare",    f"https://card.wb.ru/cards/v2/detail?nm={NM}"),
    ("v1-full",    f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&nm={NM}"),
    ("v2-full",    f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}"),
    ("v2-dest2",   f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=12358062&spp=30&nm={NM}"),
]
HDR = {
    "Accept": "application/json",
    "Referer": "https://www.wildberries.ru/",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

try:
    from curl_cffi import requests as creq
except Exception as e:
    print("нет curl_cffi:", e)
    sys.exit()

for name, url in URLS:
    try:
        r = creq.get(url, headers=HDR, impersonate="chrome120", timeout=20)
        body = (r.text or "").replace("\n", " ")
        print(f"\n[{name}] HTTP {r.status_code} | размер {len(body)}")
        print("  тело:", body[:400])
    except Exception as e:
        print(f"\n[{name}] ошибка: {str(e)[:80]}")
