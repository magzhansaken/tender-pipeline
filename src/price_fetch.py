"""
price_fetch.py — достаёт ЦЕНУ со страницы товара ПО ССЫЛКЕ.

Гибрид: DDG + сверка (Ollama) находят НУЖНЫЙ товар и дают ссылку на него,
а этот модуль по той ссылке вытаскивает РЕАЛЬНУЮ цену с маркетплейса.
Так релевантность остаётся на DDG, а цена берётся прямо со страницы товара
(в сниппете поиска её нет — это мы проверили: 0 цен из 398 лотов).

Сейчас поддержан: satu.kz (лёгкий, обычный requests, цена в тенге).
Логика извлечения цены взята 1:1 из рабочего сборщика Satu
(data-qaprice="..." и JSON "price":"...").

Запуск:
    # проверить конкретные ссылки руками
    python price_fetch.py "https://satu.kz/p123-tovar.html" "https://satu.kz/..."

    # проверить на РЕАЛЬНЫХ ссылках из базы (найденные лоты на satu.kz)
    DATABASE_URL=postgresql://tender:...@db:5432/tender python price_fetch.py --from-db 10

Зависимости: requests (+ asyncpg только для режима --from-db).
"""
import re
import sys

import requests

# Браузерные заголовки — как в рабочем сборщике Satu.
# 'br' (brotli) намеренно убран, чтобы не тянуть лишнюю зависимость.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def extract_satu_price(html):
    """
    Цена со страницы satu.kz. Та же логика, что в рабочем сборщике:
      1) data-qaprice="123456"  (самый надёжный признак цены товара)
      2) JSON "price":"123456"  (запасной)
    Возвращает int (тенге) или None.
    """
    blocks = re.findall(r'data-qaprice="(\d{3,})"', html)
    if blocks:
        return int(blocks[0])

    json_prices = re.findall(r'"price"\s*:\s*"(\d{4,})"', html)
    for p in json_prices:
        if int(p) >= 1000:        # отсекаем мусорные мелкие числа
            return int(p)

    return None


def extract_kaspi_price(html):
    """
    Цена со страницы товара kaspi.kz. Логика из рабочего сборщика Kaspi:
      1) "unitPrice": 123456   (Kaspi встраивает JSON товара в страницу — надёжно)
      2) "price":"123456"      (schema.org / JSON-LD, запасной)
      3) item-card__prices-price>123 456   (HTML, последний запасной)
    Возвращает int (тенге) или None.
    """
    m = re.search(r'"unitPrice"\s*:\s*(\d{3,})', html)
    if m:
        return int(m.group(1))

    m = re.search(r'"price"\s*:\s*"?(\d{3,})"?', html)
    if m:
        return int(m.group(1))

    m = re.search(r'item-card__prices-price[^>]*>([\d\s\u00a0]+)', html)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if digits and int(digits) >= 100:
            return int(digits)

    return None


def extract_chipdip_price(html):
    """
    Цена со страницы товара chipdip.kz. Логика из рабочего сборщика Chipdip
    (несколько методов, от самого надёжного к запасному):
      1) itemprop="price" content="10700"   (микроразметка товара — надёжно)
      2) content="10700" ... itemprop="price"
      3) <meta property="product:price:amount" content="10700">
      4) JSON "price":10700  (ловит и JSON-LD без парсинга)
      5) data-price="10700"
      6) >10 700 ₸<  (цена с тенге между тегами)
    Возвращает int (тенге) или None.
    """
    m = re.search(r'itemprop=["\']price["\'][^>]*content=["\'](\d+)["\']', html, re.I)
    if m:
        return int(m.group(1))

    m = re.search(r'content=["\'](\d+)["\'][^>]*itemprop=["\']price["\']', html, re.I)
    if m:
        return int(m.group(1))

    m = re.search(r'<meta[^>]*property=["\']product:price:amount["\'][^>]*content=["\'](\d+)', html, re.I)
    if m:
        return int(m.group(1))

    m = re.search(r'"price"\s*:\s*"?(\d{2,})"?', html)
    if m:
        return int(m.group(1))

    m = re.search(r'data-price=["\'](\d+)["\']', html)
    if m:
        return int(m.group(1))

    m = re.search(r'>(\d{1,3}(?:[\s,\u00a0]\d{3})*)\s*\u20b8<', html)
    if m:
        return int(re.sub(r"\D", "", m.group(1)))

    return None


def extract_otvertka_price(html):
    """
    Цена со страницы товара otvertka.kz (магазин на OpenCart):
      1) itemprop="price" content="28300"   (микроразметка — надёжно)
      2) content="28300" ... itemprop="price"
      3) "price":"28300"
      4) data-price="28300"
      5) отображаемая цена "28 300 тг"  (запасной)
    Возвращает int (тенге) или None.
    """
    m = re.search(r'itemprop=["\']price["\'][^>]*content=["\'](\d+)["\']', html, re.I)
    if m:
        return int(m.group(1))

    m = re.search(r'content=["\'](\d+)["\'][^>]*itemprop=["\']price["\']', html, re.I)
    if m:
        return int(m.group(1))

    m = re.search(r'"price"\s*:\s*"?(\d{3,})"?', html)
    if m:
        return int(m.group(1))

    m = re.search(r'data-price=["\'](\d+)["\']', html)
    if m:
        return int(m.group(1))

    m = re.search(r'(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{3,})\s*тг', html, re.I)
    if m:
        return int(re.sub(r"\D", "", m.group(1)))

    return None


def is_satu_product_url(url):
    """
    True только для НАСТОЯЩЕЙ карточки товара satu.kz (есть p{номер} в адресе),
    например .../p123511701-ugolnik.html или .../kz/p110908241-dvigatel.html.
    Страницы категорий/листингов (.../kz/Dizelnoe-toplivo, .../taraz/Filtr.html)
    не проходят — на них цена первого попавшегося товара, ей доверять нельзя.
    """
    return bool(re.search(r'(^|/)p\d{4,}-', url or ""))


def is_kaspi_product_url(url):
    """
    True для карточки товара kaspi.kz (адрес вида kaspi.kz/shop/p/...-12345/).
    Категории (/shop/c/...) и поиск (/shop/search/) не проходят.
    """
    return "/shop/p/" in (url or "")


def is_chipdip_product_url(url):
    """
    True для карточки товара chipdip.kz (в адресе есть /product/).
    Категории/поиск (/search) не проходят.
    """
    return "/product/" in (url or "")


def is_otvertka_product_url(url):
    """
    True для карточки товара otvertka.kz (артикул одним сегментом в адресе,
    напр. otvertka.kz/0603130020/). Категории, поиск и служебные — нет.
    """
    u = (url or "").lower()
    if "otvertka.kz" not in u:
        return False
    bad = ["/search", "/category/", "/catalog", "/katalog", "/tools/", "/about/",
           "/contact/", "/brands/", "/service-center/", "?q=", "?page=", "?sort="]
    if any(b in u for b in bad):
        return False
    path = u.split("?")[0]
    return bool(re.search(r'otvertka\.kz/[a-z0-9-]{5,}/?$', path))


# домен -> функция-извлекатель цены со страницы товара
EXTRACTORS = {
    "satu.kz": extract_satu_price,
    "kaspi.kz": extract_kaspi_price,
    "chipdip.kz": extract_chipdip_price,
    "otvertka.kz": extract_otvertka_price,
}

# домен -> функция-проверка, что ссылка ведёт на карточку товара (а не на категорию)
URL_GUARDS = {
    "satu.kz": is_satu_product_url,
    "kaspi.kz": is_kaspi_product_url,
    "chipdip.kz": is_chipdip_product_url,
    "otvertka.kz": is_otvertka_product_url,
}

# домен -> доп. заголовки (некоторым площадкам нужен Referer и т.п.)
SITE_HEADERS = {
    "kaspi.kz": {"Referer": "https://kaspi.kz/"},
}


def _wb_article_from_url(url):
    """Артикул (nmId) из ссылки-карточки Wildberries."""
    u = url or ""
    m = re.search(r"/catalog/(\d{4,})/detail", u)
    if m:
        return m.group(1)
    m = re.search(r"/catalog/(\d{6,})", u)
    if m:
        return m.group(1)
    return None


def fetch_wb_price(url, timeout=20):
    """
    Цена с Wildberries по ссылке-карточке — БЕСПЛАТНО через открытый JSON-API WB
    по артикулу из ссылки (без браузера). Цена в РУБЛЯХ (publish пересчитает ×курс).
    Берёт salePriceU/priceU или новый sizes[].price (в копейках, ÷100).
    Возвращает (price_rub|None, note).
    """
    article = _wb_article_from_url(url)
    if not article:
        return None, "wb: нет артикула в ссылке"
    endpoints = (
        f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={article}",
        f"https://card.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&nm={article}",
    )
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        "Accept": "application/json",
    }
    last = "wb: не удалось"
    for api in endpoints:
        try:
            resp = requests.get(api, headers=headers, timeout=timeout)
        except Exception as e:
            last = f"wb: сеть {str(e)[:40]}"
            continue
        if resp.status_code != 200:
            last = f"wb: HTTP {resp.status_code}"
            continue
        try:
            products = resp.json().get("data", {}).get("products", [])
        except Exception:
            last = "wb: не JSON"
            continue
        if not products:
            last = "wb: товар не найден в API"
            continue
        p = products[0]
        # новый формат: sizes[].price.{product,total} (копейки)
        for sz in p.get("sizes", []):
            pr = sz.get("price") or {}
            raw = pr.get("product") or pr.get("total")
            if raw:
                return int(round(raw / 100)), "wb:sizes.price"
        # старый формат: salePriceU / priceU (копейки)
        raw = p.get("salePriceU") or p.get("priceU")
        if raw:
            return int(round(raw / 100)), "wb:salePriceU"
        last = "wb: цена не найдена в ответе"
    return None, last


def fetch_price(url, timeout=20):
    """
    Фетчит страницу товара по ссылке и достаёт цену.
    Возвращает (price|None, note) — note для диагностики.
    """
    if not url:
        return None, "пустая ссылка"

    # Wildberries — через открытый JSON-API по артикулу (не HTML-скрейпинг)
    if "wildberries.ru" in url:
        return fetch_wb_price(url, timeout)

    site = next((d for d in EXTRACTORS if d in url), None)
    if not site:
        return None, "нет извлекателя для этой площадки"

    guard = URL_GUARDS.get(site)
    if guard and not guard(url):
        return None, "не карточка товара (категория/листинг) — цену пропускаем"

    headers = dict(HEADERS)
    headers.update(SITE_HEADERS.get(site, {}))

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except Exception as e:
        return None, f"ошибка сети: {str(e)[:60]}"

    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"

    price = EXTRACTORS[site](resp.text)
    if price is None:
        return None, "цена не найдена на странице"
    return price, "ok"


HEAVY_SITES = ("ozon.", "wildberries.", "yandex.")


def is_heavy_site(url):
    """True для тяжёлых сайтов (ozon/wildberries/yandex), где цену со страницы
    дёшево не взять — только там подключаем поиск Олламы как ориентир."""
    u = (url or "").lower()
    return any(h in u for h in HEAVY_SITES)


def fetch_price_by_name_ollama(name, timeout=45):
    """
    Цена-ОРИЕНТИР через поиск Олламы — fallback ТОЛЬКО для тяжёлых сайтов
    (ozon/wb/yandex), где цену со страницы не взять. Ищет товар по названию
    в любом магазине, берёт первую цену: ₸ в приоритете, иначе руб->₸.
    ТРАТИТ лимит Олламы — вызывать только по тяжёлым «дыркам».
    Возвращает (price_kzt|None, note). Цена уже в тенге.
    """
    import os
    key = os.getenv("OLLAMA_API_KEY")
    if not key or not name:
        return None, "ollama: нет ключа или названия"
    rate_rub = float(os.getenv("RATE_RUB", "5.0"))
    try:
        resp = requests.post(
            "https://ollama.com/api/web_search",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"query": f"{name} купить цена"},
            timeout=timeout,
        )
    except Exception as e:
        return None, f"ollama: сеть {str(e)[:40]}"
    if resp.status_code != 200:
        return None, f"ollama: HTTP {resp.status_code}"
    try:
        results = resp.json().get("results", [])
    except Exception:
        return None, "ollama: не JSON"

    for res in results:
        content = res.get("content", "") or ""
        m = re.search(r"(\d[\d\s\u2009\u00a0]{2,})\s*\u20b8", content)
        if m:
            v = int(re.sub(r"\D", "", m.group(1)))
            if 100 < v < 50_000_000:
                return v, "ollama:₸"
        m = re.search(r"(\d[\d\s\u2009\u00a0]{2,})\s*(?:руб|₽|р\.)", content, re.I)
        if m:
            v = int(re.sub(r"\D", "", m.group(1)))
            if 100 < v < 50_000_000:
                return int(round(v * rate_rub)), "ollama:руб→₸"
    return None, "ollama: цена не найдена"


def _fmt(price):
    return (f"{price:,}".replace(",", " ") + " \u20b8") if price else "\u2014"


def run_from_db(n):
    """Тест на реальных ссылках из базы: берёт найденные лоты на ПОДДЕРЖАННЫХ
    площадках (satu.kz, kaspi.kz, ...) и пробует достать цену по каждой ссылке."""
    import os
    import asyncio
    import asyncpg

    db = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")

    async def go():
        conn = await asyncpg.connect(db)
        rows = await conn.fetch(
            "SELECT split_part(lot_number,'-',1) AS lot, "
            "       COALESCE(found_url, match_result->>'source_url') AS url, "
            "       match_result->>'product_name' AS prod "
            "FROM tenders "
            "WHERE match_status IN ('FOUND_EXACT','FOUND_PARTIAL') "
            "  AND COALESCE(found_url, match_result->>'source_url') IS NOT NULL "
            "ORDER BY collected_at DESC LIMIT 600"
        )
        await conn.close()
        return rows

    rows = asyncio.run(go())
    # оставляем только ссылки на поддержанные площадки
    supported = [r for r in rows if any(d in (r["url"] or "") for d in EXTRACTORS)]
    supported = supported[:n]

    if not supported:
        print("В базе нет найденных лотов со ссылками на поддержанные площадки "
              f"({', '.join(EXTRACTORS)}).")
        return

    print(f"Проверяю {len(supported)} ссылок ({', '.join(EXTRACTORS)}):\n")
    ok = 0
    for r in supported:
        site = next((d for d in EXTRACTORS if d in (r["url"] or "")), "?")
        price, note = fetch_price(r["url"])
        if price:
            ok += 1
        print(f"  лот {r['lot']:<10} {site:<9} {_fmt(price):>14}  [{note}]  {(r['prod'] or '')[:32]}")
        print(f"      {(r['url'] or '')[:95]}")
    print(f"\nИтог: цена достана у {ok} из {len(supported)}.")


def run_wb_from_db(n):
    """Тест WB на реальных ссылках из базы: берёт найденные wildberries.ru-лоты
    и пробует достать цену через открытый API по каждой ссылке."""
    import os
    import asyncio
    import asyncpg

    db = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")

    async def go():
        conn = await asyncpg.connect(db)
        rows = await conn.fetch(
            "SELECT COALESCE(found_url, match_result->>'source_url') AS url, "
            "       match_result->>'product_name' AS prod "
            "FROM tenders "
            "WHERE match_status IN ('FOUND_EXACT','FOUND_PARTIAL') "
            "  AND COALESCE(found_url, match_result->>'source_url') ILIKE '%wildberries.ru%' "
            "ORDER BY collected_at DESC LIMIT $1",
            n,
        )
        await conn.close()
        return rows

    rows = asyncio.run(go())
    if not rows:
        print("В базе нет wildberries.ru-ссылок.")
        return

    print(f"Проверяю {len(rows)} ссылок Wildberries (цена в ₽):\n")
    ok = 0
    for r in rows:
        price, note = fetch_wb_price(r["url"])
        if price:
            ok += 1
        print(f"  {(_fmt(price) if price else '—'):>12} ₽  [{note}]  {(r['prod'] or '')[:30]}")
        print(f"      {(r['url'] or '')[:90]}")
    print(f"\nИтог: цена достана у {ok} из {len(rows)}.")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--from-db":
        run_from_db(int(args[1]) if len(args) > 1 else 10)
        return
    if args[0] == "--wb-from-db":
        run_wb_from_db(int(args[1]) if len(args) > 1 else 8)
        return
    for url in args:
        price, note = fetch_price(url)
        print(f"{_fmt(price):>14}  [{note}]  {url}")


if __name__ == "__main__":
    main()
