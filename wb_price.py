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


def _price_from_card_json(data):
    """Цена (₽) из ответа card.wb.ru: sizes[].price или salePriceU/priceU (копейки)."""
    try:
        products = (data.get("data") or {}).get("products") or []
    except Exception:
        return None
    if not products:
        return None
    p = products[0]
    for sz in p.get("sizes", []):
        pr = sz.get("price") or {}
        raw = pr.get("product") or pr.get("total")
        if raw:
            return round(raw / 100)
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

    def _on_response(self, response):
        u = response.url
        if ("card.wb.ru" in u or "u-card.wb.ru" in u) and "detail" in u:
            try:
                self._captured = response.json()
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
        self.page.set_extra_http_headers({
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
        })
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
        """Открыть карточку по ссылке и вернуть (price_rub|None, note)."""
        for attempt in range(retry + 1):
            try:
                if self.count % 3 == 0 or not self.browser:
                    self._start()
                    self._warm_up()
                self.count += 1
                self._captured = None
                self._delay(1, 3)
                self.page.goto(url, wait_until='domcontentloaded', timeout=60000)
                self._delay(3, 5)
                try:
                    self.page.evaluate('window.scrollBy(0, 400)')
                except Exception:
                    pass
                self._delay(1, 2)

                # 1) цена из перехваченного ответа card.wb.ru
                if self._captured:
                    pr = _price_from_card_json(self._captured)
                    if pr:
                        return pr, "api"

                # 2) запасной разбор из HTML карточки
                for sel in DOM_PRICE_SELECTORS:
                    try:
                        el = self.page.query_selector(sel)
                    except Exception:
                        el = None
                    if el:
                        txt = (el.inner_text() or "").replace('\xa0', ' ')
                        m = re.search(r'[\d\s]{2,}', txt)
                        if m:
                            v = re.sub(r'\D', '', m.group())
                            if v and 10 < int(v) < 50_000_000:
                                return int(v), f"dom:{sel}"
                return None, "цена не найдена на странице"
            except Exception as e:
                self._stop()  # перезапуск на следующей попытке
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
    """Открыть одну карточку и показать ВСЕ запросы WB + что в них, чтобы найти,
    где лежит цена. Печатает url-ы api и куски тел, где встречается цена."""
    if sync_playwright is None:
        print("Playwright не установлен.")
        return
    print(f"DEBUG карточки:\n  {url}\n")
    seen = []

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
        u = resp.url
        # интересны только домены WB, похожие на данные о товаре
        if any(k in u for k in ('wb.ru', 'wbbasket.ru', 'basket-')) and \
           any(k in u for k in ('detail', 'card', 'price', 'nm')):
            ct = (resp.headers or {}).get('content-type', '')
            body = ''
            if 'json' in ct or u.endswith('.json'):
                try:
                    body = resp.text()[:600]
                except Exception:
                    body = '<не прочитать>'
            seen.append((resp.status, u, body))

    page.on('response', on_resp)

    # прогрев
    try:
        page.goto('https://www.wildberries.ru/', wait_until='domcontentloaded', timeout=60000)
        time.sleep(3)
    except Exception:
        pass
    # карточка
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
        time.sleep(6)
        page.evaluate('window.scrollBy(0, 600)')
        time.sleep(3)
    except Exception as e:
        print("ошибка загрузки:", str(e)[:80])

    print(f"=== Запросы WB, похожие на данные о товаре ({len(seen)}) ===")
    for status, u, body in seen:
        print(f"\n[{status}] {u[:130]}")
        if body:
            flat = body.replace('\n', ' ')
            print(f"   тело: {flat[:300]}")
            # подсветим, если в теле есть похожее на цену
            import re as _re
            for kw in ('salePriceU', 'priceU', '"price"', 'product":', 'total":'):
                if kw in body:
                    m = _re.search(_re.escape(kw) + r'[\":\s]*\d+', body)
                    if m:
                        print(f"   ★ {m.group()[:40]}")

    # заодно глянем, что в DOM похоже на цену
    print("\n=== Элементы DOM с 'price' в классе ===")
    try:
        nodes = page.query_selector_all('[class*="price"]')
        shown = 0
        for nd in nodes:
            try:
                cls = nd.get_attribute('class') or ''
                txt = (nd.inner_text() or '').strip().replace('\xa0', ' ')
            except Exception:
                continue
            if txt and any(ch.isdigit() for ch in txt) and shown < 12:
                print(f"   .{cls[:45]:45} -> {txt[:30]}")
                shown += 1
    except Exception as e:
        print("  dom-проба не удалась:", str(e)[:60])

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
