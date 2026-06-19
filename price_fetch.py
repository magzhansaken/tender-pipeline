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


# домен -> функция-извлекатель (позже добавим kaspi и др.)
EXTRACTORS = {
    "satu.kz": extract_satu_price,
}


def fetch_price(url, timeout=20):
    """
    Фетчит страницу товара по ссылке и достаёт цену.
    Возвращает (price|None, note) — note для диагностики.
    """
    if not url:
        return None, "пустая ссылка"

    site = next((d for d in EXTRACTORS if d in url), None)
    if not site:
        return None, "нет извлекателя для этой площадки (пока только satu.kz)"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:
        return None, f"ошибка сети: {str(e)[:60]}"

    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"

    price = EXTRACTORS[site](resp.text)
    if price is None:
        return None, "цена не найдена на странице"
    return price, "ok"


def _fmt(price):
    return (f"{price:,}".replace(",", " ") + " \u20b8") if price else "\u2014"


def run_from_db(n):
    """Тест на реальных ссылках из базы: берёт найденные лоты на satu.kz и пробует цену."""
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
            "  AND COALESCE(found_url, match_result->>'source_url') LIKE '%satu.kz%' "
            "ORDER BY collected_at DESC LIMIT $1",
            n,
        )
        await conn.close()
        return rows

    rows = asyncio.run(go())
    if not rows:
        print("В базе нет найденных лотов со ссылкой на satu.kz.")
        return

    print(f"Проверяю {len(rows)} ссылок на satu.kz:\n")
    ok = 0
    for r in rows:
        price, note = fetch_price(r["url"])
        if price:
            ok += 1
        print(f"  лот {r['lot']:<10} {_fmt(price):>14}  [{note}]  {(r['prod'] or '')[:38]}")
        print(f"      {(r['url'] or '')[:95]}")
    print(f"\nИтог: цена достана у {ok} из {len(rows)}.")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--from-db":
        run_from_db(int(args[1]) if len(args) > 1 else 10)
        return
    for url in args:
        price, note = fetch_price(url)
        print(f"{_fmt(price):>14}  [{note}]  {url}")


if __name__ == "__main__":
    main()
