"""
ozon_probe.py — ДЕШЁВАЯ проверка: можно ли взять Ozon без тяжёлого браузера?

Пробует с серверного IP:
  1) внутренний API Ozon (composer-api, entrypoint-api),
  2) прямой заход на страницу поиска.
И смотрит: какой статус, пришёл ли JSON, есть ли в ответе похожие на цену числа.

Если хоть один способ вернёт 200 + цены -> Ozon реально сделать на requests, без Chromium.
Если везде 403 / нет цен -> нужен Playwright или платный сервис.

    python ozon_probe.py
    python ozon_probe.py "кабель ВВГ 3х2.5"
"""
import re
import sys
from urllib.parse import quote

import requests

QUERY = sys.argv[1] if len(sys.argv) > 1 else "ноутбук lenovo"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://ozon.kz",
    "Referer": "https://ozon.kz/",
}

# что пробуем (и API, и прямой заход)
TARGETS = [
    ("composer-api", f"https://ozon.kz/api/composer-api.bx/page/json/v2?url=/search/?text={quote(QUERY)}"),
    ("entrypoint-api", f"https://ozon.kz/api/entrypoint-api.bx/page/json/v2?url=/search/?text={quote(QUERY)}"),
    ("прямой поиск", f"https://ozon.kz/search/?text={quote(QUERY)}&from_global=true"),
]


def looks_like_prices(text):
    # числа рядом с ₸ или поля price в JSON
    near_tenge = re.findall(r"(\d[\d\s\u2009\u00a0]{3,})\s*\u20b8", text)
    price_fields = re.findall(r'"[^"]*[Pp]rice[^"]*"\s*:\s*"?\d{3,}', text)
    sample = [p.strip() for p in near_tenge[:5]]
    return len(near_tenge), len(price_fields), sample


def main():
    print(f"Запрос: {QUERY!r}\n")
    for name, url in TARGETS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            is_json = "application/json" in r.headers.get("Content-Type", "")
            n_tenge, n_price, sample = looks_like_prices(r.text)
            verdict = "✅ есть цены" if (n_tenge or n_price) else "— цен не видно"
            print(f"[{name:14}] HTTP {r.status_code} | размер {len(r.text):>7} | "
                  f"json={is_json} | ₸-чисел={n_tenge} price-полей={n_price} | {verdict}")
            if sample:
                print(f"                 примеры чисел: {sample}")
        except Exception as e:
            print(f"[{name:14}] ошибка: {str(e)[:70]}")
    print("\nИтог: если где-то HTTP 200 и есть цены — Ozon можно без Chromium.")
    print("Если везде 403 или цен нет — нужен Playwright или платный сервис.")


if __name__ == "__main__":
    main()
