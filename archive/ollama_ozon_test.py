"""
ollama_ozon_test.py — проверка: достаёт ли ПОИСК ОЛЛАМЫ цены Ozon?

Идея: поиск Олламы выполняется на серверах Олламы (их IP), а не на нашем
заблокированном датацентровом. Значит он может обойти блок Ozon.

Проверяем оба пути:
  1) web_search по запросу про Ozon — есть ли в результатах ozon-ссылки и цены (₸)?
  2) web_fetch страницы ozon.kz — вернётся ли контент, и будет ли в нём цена?

Нужен OLLAMA_API_KEY (берётся из окружения / .env). Ставит только requests.

    python ollama_ozon_test.py
    python ollama_ozon_test.py "кабель ВВГ 3х2.5"
"""
import os
import re
import sys
from urllib.parse import quote

import requests

KEY = os.getenv("OLLAMA_API_KEY")
QUERY = sys.argv[1] if len(sys.argv) > 1 else "ноутбук Lenovo IdeaPad"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def prices(text):
    out = []
    for m in re.findall(r"(\d[\d\s\u2009\u00a0]{3,})\s*\u20b8", text or ""):
        c = re.sub(r"[\s\u2009\u00a0]", "", m)
        if c.isdigit() and 100 < int(c) < 50_000_000:
            out.append(int(c))
    return out


def call(path, payload):
    r = requests.post(f"https://ollama.com/api/{path}", headers=H, json=payload, timeout=45)
    ct = r.headers.get("content-type", "")
    return r.status_code, (r.json() if ct.startswith("application/json") else r.text)


def main():
    if not KEY:
        print("НЕТ OLLAMA_API_KEY в окружении — запусти с --env-file /opt/tenderview/.env")
        return

    # ── 1) web_search ────────────────────────────────────────────────
    print(f"=== 1) web_search Олламы: '{QUERY} ozon.kz цена' ===")
    try:
        st, data = call("web_search", {"query": f"{QUERY} ozon.kz цена"})
        print(f"HTTP {st}")
        results = data.get("results", []) if isinstance(data, dict) else []
        print(f"результатов: {len(results)}")
        ozon_hits = price_hits = 0
        for r in results:
            url = r.get("url", "")
            p = prices(r.get("content", ""))
            is_oz = "ozon." in url
            ozon_hits += is_oz
            price_hits += bool(p)
            print(f"  [{'OZON' if is_oz else '    '}] {url[:58]:60} цены: {p[:3] if p else '—'}")
        print(f"  -> ozon-ссылок: {ozon_hits} | результатов с ценой ₸: {price_hits}")
    except Exception as e:
        print("ошибка web_search:", str(e)[:160])

    # ── 2) web_fetch ─────────────────────────────────────────────────
    print(f"\n=== 2) web_fetch Олламы: страница поиска ozon.kz ===")
    try:
        url = f"https://ozon.kz/search/?text={quote(QUERY)}&from_global=true"
        st, data = call("web_fetch", {"url": url})
        print(f"HTTP {st}")
        content = data.get("content", "") if isinstance(data, dict) else str(data)
        title = data.get("title", "") if isinstance(data, dict) else ""
        p = prices(content)
        print(f"  заголовок: {title[:70]}")
        print(f"  размер контента: {len(content)}")
        print(f"  цен (₸) найдено: {len(p)}  {p[:8] if p else ''}")
        low = content.lower()
        if not p and ("доступ огранич" in low or "captcha" in low or "соединени" in low):
            print("  ⚠️ похоже, Ozon отдал заглушку даже Олламе")
    except Exception as e:
        print("ошибка web_fetch:", str(e)[:160])

    print("\nИтог:")
    print("  Если в (1) или (2) есть цены ₸ — поиск Олламы пробивает Ozon, прокси НЕ нужен. 🎉")
    print("  Если цен нет нигде — Олламе цена тоже не отдаётся, для Ozon всё равно нужен прокси/браузер.")


if __name__ == "__main__":
    main()
