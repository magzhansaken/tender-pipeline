"""
ozon_fetch.py — ИЗОЛИРОВАННЫЙ тест: берём цену с Ozon.kz через Playwright (Chromium).

Это ПРОВЕРКА в изоляции, НЕ часть пайплайна. Цель — убедиться, что:
  - Playwright + Chromium ставятся и запускаются на сервере,
  - Ozon пускает Chromium с серверного IP (API/прямой заход уже давали 403),
  - страница отдаёт цену после JS,
  - сервер это переживает по памяти.

Схема браузера взята 1:1 из рабочего сборщика (ozon_final_parser.py).

Режимы:
  python ozon_fetch.py "ноутбук lenovo"                    # поиск -> первый товар + цены
  python ozon_fetch.py "https://ozon.kz/product/...-123/"   # цена прямо со страницы товара

Зависимости (ставятся в контейнере):
  pip install playwright playwright-stealth
  playwright install chromium && playwright install-deps chromium
"""
import re
import sys
import time
from urllib.parse import quote

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# цена Ozon: число (с обычными и thin-space пробелами) + ₸  — паттерн из рабочего сборщика
PRICE_RE = r'(\d[\d\s\u2009\u00a0]*)\s*\u20b8'


def extract_prices(html):
    """Все осмысленные цены (₸) со страницы, в порядке появления."""
    out = []
    for m in re.findall(PRICE_RE, html):
        clean = re.sub(r'[\s\u2009\u00a0]', '', m)
        if clean.isdigit():
            v = int(clean)
            if 100 < v < 50_000_000:
                out.append(v)
    return out


def get_html(url):
    from playwright.sync_api import sync_playwright
    try:
        from playwright_stealth import stealth_sync
    except Exception:
        stealth_sync = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=LAUNCH_ARGS)
        try:
            ctx = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=UA,
                locale="ru-RU",
            )
            page = ctx.new_page()
            if stealth_sync:
                try:
                    stealth_sync(page)
                except Exception:
                    pass
            page.goto(url, wait_until="networkidle", timeout=40000)
            page.wait_for_timeout(3000)
            return page.content()
        finally:
            browser.close()


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "ноутбук lenovo"
    url = arg if arg.startswith("http") else f"https://ozon.kz/search/?text={quote(arg)}&from_global=true"

    print(f"Открываю: {url}")
    print("(запускается Chromium — первый раз это несколько минут на установку)\n")
    t = time.time()
    try:
        html = get_html(url)
    except Exception as e:
        print(f"ОШИБКА запуска/захода: {str(e)[:200]}")
        return

    prices = extract_prices(html)
    mt = re.search(r"<title>([^<]+)</title>", html)
    title = mt.group(1).strip() if mt else ""

    print(f"Готово за {time.time() - t:.1f}с | размер HTML: {len(html)}")
    print(f"Заголовок страницы: {title[:90]}")
    print(f"Найдено цен (₸): {len(prices)}")
    if prices:
        print(f"  первые: {prices[:8]}")
        print(f"  минимальная: {min(prices)}")
        print(f"  ВЕРОЯТНАЯ цена товара (первая): {prices[0]} \u20b8")
    else:
        print("  Цен не найдено.")
        low = html.lower()
        if "доступ ограничен" in low or "captcha" in low or "antibot" in low or "проверка" in low:
            print("  ⚠️ Похоже на анти-бот блокировку / капчу — значит и Chromium с этого IP не пускают.")
        else:
            print("  Возможно, страница не догрузилась (увеличить ожидание) или изменилась разметка.")


if __name__ == "__main__":
    main()
