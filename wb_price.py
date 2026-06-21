#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb_price.py — НАСТОЯЩАЯ цена Wildberries по ГОТОВОЙ ссылке (которую нашёл DDG).

Открывает карточку товара stealth-браузером (рецепт 1:1 из рабочего
wb_stealth_parser.py: Москва, ru-RU, прогрев на главной, stealth-скрипт) и
ловит ответ card.wb.ru, который карточка сама вызывает — там цена в копейках.
Запасной путь: разбор цены из HTML карточки.

Это ТЯЖЁЛЫЙ браузерный способ (~10-15с на товар) — для отдельного медленного
прохода по WB-лотам, НЕ для быстрого поиска.

CLI:
    python wb_price.py "https://www.wildberries.ru/catalog/377420616/detail.aspx"
    python wb_price.py --from-db 8      # тест на реальных WB-ссылках из базы

Зависимости: playwright (+ chromium), для --from-db ещё asyncpg.
"""
import re
import sys
import time
import json
import random

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });
    window.chrome = { runtime: {} };
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
    Object.defineProperty(navigator, 'productSub', { get: () => '20030107' });
    Object.defineProperty(navigator, 'userAgentData', {
        get: () => ({
            brands: [
                { brand: 'Google Chrome', version: '120' },
                { brand: 'Chromium', version: '120' },
                { brand: 'Not_A Brand', version: '24' }
            ],
            mobile: false,
            platform: 'Windows'
        })
    });
}
"""

LAUNCH_ARGS = [
    '--disable-blink-features=AutomationControlled',
    '--disable-features=IsolateOrigins,site-per-process',
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-dev-shm-usage',
    '--disable-accelerated-2d-canvas',
    '--no-first-run',
    '--no-zygote',
    '--disable-gpu',
    '--window-size=1920,1080',
]

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

DOM_PRICE_SELECTORS = [
    'ins.price-block__final-price',
    '.price-block__final-price',
    '.price-block__wallet-price',
    '.product-page__price-block .price-block__final-price',
]


def _wb_article_from_url(url):
    """Артикул (nmId) из ссылки Wildberries."""
    u = url or ""
    m = re.search(r"/catalog/(\d{4,})/detail", u)
    if m:
        return m.group(1)
    m = re.search(r"/catalog/(\d{6,})", u)
    if m:
        return m.group(1)
    m = re.search(r"/product/[^/]*?(\d{6,})", u)  # global.wildberries.ru/product/...-NNNN
    if m:
        return m.group(1)
    return None


def _price_from_card_json(data):
    """Цена (₽) из ответа WB. Новый формат: products[].price.product (копейки),
    старый: sizes[].price.* или salePriceU/priceU (копейки)."""
    try:
        products = (data.get("data") or {}).get("products") or []
    except Exception:
        products = []
    if not products:
        return None
    p = products[0]
    # новый формат: price.{product,total,basic}
    pr = p.get("price") or {}
    raw = pr.get("product") or pr.get("total") or pr.get("basic")
    if raw:
        return round(raw / 100)
    # формат с размерами
    for sz in p.get("sizes", []):
        spr = sz.get("price") or {}
        raw = spr.get("product") or spr.get("total") or spr.get("basic")
        if raw:
            return round(raw / 100)
    # старый формат
    raw = p.get("salePriceU") or p.get("priceU")
    if raw:
        return round(raw / 100)
    return None


class WBPriceFetcher:
    def __init__(self, headless=True):
        self.headless = headless
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self._captured = None
        self.count = 0
        self._resp_count = 0
        self._hit_count = 0

    def _on_response(self, response):
        u = response.url
        self._resp_count += 1
        # WB переехал на /__internal/u-card/cards/v4/{detail,list}; плюс старые адреса
        if ('u-card/cards/' in u and ('detail' in u or 'list' in u)) or \
           ('search.wb.ru' in u and 'search' in u) or \
           (('card.wb.ru' in u or 'u-card.wb.ru' in u) and 'detail' in u):
            try:
                body = response.text()          # как в рабочем debug
                self._captured = json.loads(body)
                self._hit_count += 1
            except Exception:
                pass

    def _start(self):
        self._stop()
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=self.headless, args=LAUNCH_ARGS)
        self.context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=UA,
            locale='ru-RU',
            timezone_id='Europe/Moscow',
            geolocation={'latitude': 55.7558, 'longitude': 37.6173},
            permissions=['geolocation'],
            color_scheme='light',
            java_script_enabled=True,
            is_mobile=False,
        )
        self.context.add_init_script(STEALTH_JS)
        self.page = self.context.new_page()
        # ВАЖНО: не подставляем Sec-Ch-Ua вручную — реальный Chrome шлёт свои,
        # а антибот WB сверяет их с движком. Ставим только язык (как в рабочем debug).
        self.page.set_extra_http_headers({'Accept-Language': 'ru-RU,ru;q=0.9'})
        self.page.on('response', self._on_response)

    def _stop(self):
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.pw:
                self.pw.stop()
        except Exception:
            pass
        self.page = self.context = self.browser = self.pw = None

    def _delay(self, a=1.0, b=3.0):
        time.sleep(random.uniform(a, b))

    def _warm_up(self):
        try:
            self.page.goto('https://www.wildberries.ru/', wait_until='domcontentloaded', timeout=60000)
            self._delay(2, 4)
            self.page.evaluate('window.scrollBy(0, 500)')
            self._delay(1, 2)
        except Exception:
            pass

    def fetch(self, url, retry=1):
        """Цена по ссылке: берём АРТИКУЛ из ссылки и ищем его через search.wb.ru
        (поиск работает на сервере, в отличие от прямого захода на карточку).
        Среди результатов берём товар с тем же артикулом. Возврат (price_rub|None, note)."""
        article = _wb_article_from_url(url)
        if not article:
            return None, "нет артикула в ссылке"
        art = int(article)

        for attempt in range(retry + 1):
            try:
                if self.count % 3 == 0 or not self.browser:
                    self._start()
                    self._warm_up()
                self.count += 1
                self._captured = None
                self._resp_count = 0
                self._hit_count = 0
                self._delay(2, 4)

                # Ищем по артикулу — выдача отдаёт JSON через u-card
                search_url = f"https://www.wildberries.ru/catalog/0/search.aspx?search={art}"
                self.page.goto(search_url, wait_until='domcontentloaded', timeout=60000)
                self._delay(5, 7)          # ждём прохождения антибота WB
                try:
                    self.page.evaluate('window.scrollBy(0, 400)')
                except Exception:
                    pass
                self._delay(3, 4)          # ждём подгрузки цены u-card

                data = self._captured
                if data:
                    products = (data.get('data') or {}).get('products') or []
                    chosen = None
                    for p in products:
                        if p.get('id') == art or p.get('nmId') == art or p.get('nm') == art:
                            chosen = p
                            break
                    # ищем по точному артикулу — если точного id нет, берём первый
                    if chosen is None and products:
                        chosen = products[0]
                    if chosen:
                        pr = _price_from_card_json({"data": {"products": [chosen]}})
                        if pr:
                            idv = chosen.get('id') or chosen.get('nmId') or chosen.get('nm')
                            exact = "точный" if idv == art else f"id={idv}"
                            return pr, f"v4 ({exact})"
                        return None, "товар есть, цена не разобрана"
                return None, f"нет данных (ответов:{self._resp_count}, перехвачено:{self._hit_count})"
            except Exception as e:
                self._stop()
                last = f"ошибка: {str(e)[:60]}"
        return None, last


def run_from_db(n):
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
            "  AND COALESCE(found_url, match_result->>'source_url') ILIKE '%wildberries.ru/catalog/%' "
            "ORDER BY collected_at DESC LIMIT $1",
            n,
        )
        await conn.close()
        return rows

    rows = asyncio.run(go())
    if not rows:
        print("В базе нет ссылок wildberries.ru/catalog/.")
        return

    print(f"Проверяю {len(rows)} WB-ссылок через stealth-браузер (цена в ₽):\n")
    fetcher = WBPriceFetcher(headless=True)
    ok = 0
    try:
        for i, r in enumerate(rows, 1):
            t = time.time()
            price, note = fetcher.fetch(r["url"])
            dt = time.time() - t
            if price:
                ok += 1
            shown = f"{price:,} ₽".replace(",", " ") if price else "—"
            print(f"  [{i}/{len(rows)}] {shown:>12}  ({dt:.0f}с, {note})  {(r['prod'] or '')[:30]}")
            print(f"        {(r['url'] or '')[:80]}")
    finally:
        fetcher._stop()
    print(f"\nИтог: цена достана у {ok} из {len(rows)}.")


def debug_one(url):
    """ГЛУБОКИЙ разбор: ловим ВСЕ ответы (без фильтра), сохраняем HTML целиком,
    ищем цену в HTML и в телах ответов. Цель — понять структуру WB на сервере."""
    if sync_playwright is None:
        print("Playwright не установлен.")
        return

    art = _wb_article_from_url(url)
    print(f"DEBUG WB\n  ссылка: {url}\n  артикул: {art}\n")

    all_resp = []   # (status, url, ctype, len)
    json_hits = []  # (url, кусок тела с ценой)

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
    context = browser.new_context(
        viewport={'width': 1920, 'height': 1080}, user_agent=UA,
        locale='ru-RU', timezone_id='Europe/Moscow',
        geolocation={'latitude': 55.7558, 'longitude': 37.6173},
        permissions=['geolocation'], color_scheme='light',
    )
    context.add_init_script(STEALTH_JS)
    page = context.new_page()
    page.set_extra_http_headers({'Accept-Language': 'ru-RU,ru;q=0.9'})

    def on_resp(resp):
        try:
            u = resp.url
            ct = (resp.headers or {}).get('content-type', '')
            body = ''
            if 'json' in ct:
                try:
                    body = resp.text()
                except Exception:
                    body = ''
            all_resp.append((resp.status, u[:110], ct[:25], len(body)))
            # ищем цену в JSON-телах
            if body and any(k in body for k in ('salePriceU', 'priceU', '"price"', '"total"', '"product"')):
                import re as _re
                snip = ''
                m = _re.search(r'.{0,30}(salePriceU|priceU|"price"|"total"|"product")["\s:]*\d+.{0,20}', body)
                if m:
                    snip = m.group(0)
                json_hits.append((u[:90], snip[:120]))
        except Exception:
            pass

    page.on('response', on_resp)

    print(">>> прогрев главной...")
    try:
        page.goto('https://www.wildberries.ru/', wait_until='domcontentloaded', timeout=60000)
        time.sleep(3)
    except Exception as e:
        print("  главная:", str(e)[:70])

    # ПОИСК по артикулу
    search_url = f"https://www.wildberries.ru/catalog/0/search.aspx?search={art}"
    print(f">>> поиск по артикулу: {search_url}")
    try:
        page.goto(search_url, wait_until='domcontentloaded', timeout=60000)
        time.sleep(6)
        page.evaluate('window.scrollBy(0, 500)')
        time.sleep(3)
    except Exception as e:
        print("  поиск:", str(e)[:70])

    html = ''
    try:
        html = page.content()
    except Exception:
        pass

    # 1) ВСЕ ответы
    print(f"\n=== ВСЕ ответы браузера: {len(all_resp)} ===")
    for st, u, ct, ln in all_resp[:40]:
        print(f"  [{st}] {ln:>7}b {ct:25} {u}")

    # 2) где в JSON встретилась цена
    print(f"\n=== JSON-тела с ценой: {len(json_hits)} ===")
    for u, snip in json_hits[:15]:
        print(f"  {u}\n     {snip}")

    # 3) что в HTML
    print(f"\n=== HTML страницы поиска: {len(html)} символов ===")
    low = html.lower()
    print(f"  есть 'товары не найдены': {'товары не найдены' in low or 'ничего не найдено' in low}")
    print(f"  есть 'каптча/captcha/доступ': {'captcha' in low or 'доступ ограничен' in low or 'robot' in low}")
    print(f"  есть 'product-card': {'product-card' in low}")
    print(f"  есть 'price': {'price' in low}")
    print(f"  заголовок страницы: {html[html.find('<title>')+7:html.find('</title>')][:80] if '<title>' in html else '—'}")
    # подсветим цену прямо из HTML, если есть
    import re as _re
    rub = _re.findall(r'(\d[\d\s\u00a0]{2,})\s*₽', html)
    print(f"  числа перед ₽ в HTML (первые 8): {rub[:8]}")

    # сохраним HTML, чтобы при желании посмотреть глазами
    try:
        with open('/app/wb_debug.html', 'w', encoding='utf-8') as f:
            f.write(html)
        print("\n  HTML сохранён: /app/wb_debug.html (можно открыть/скопировать)")
    except Exception:
        pass

    try:
        page.close(); context.close(); browser.close(); pw.stop()
    except Exception:
        pass


def main():
    if sync_playwright is None:
        print("Playwright не установлен. pip install playwright && playwright install chromium")
        return
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--from-db":
        run_from_db(int(args[1]) if len(args) > 1 else 8)
        return
    if args[0] == "--debug":
        debug_url = args[1] if len(args) > 1 else "https://www.wildberries.ru/catalog/73193253/detail.aspx"
        debug_one(debug_url)
        return

    url = args[0]
    fetcher = WBPriceFetcher(headless=True)
    try:
        t = time.time()
        price, note = fetcher.fetch(url)
        dt = time.time() - t
        if price:
            print(f"\nЦЕНА: {price:,} ₽".replace(",", " ") + f"   ({dt:.0f}с, {note})")
        else:
            print(f"\nЦена не получена ({note})")
    finally:
        fetcher._stop()


if __name__ == "__main__":
    main()
