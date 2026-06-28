#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ali_browser_probe.py — РЕШАЮЩИЙ тест AliExpress.com живым браузером
с серверного IP (наш рецепт: прогрев → поиск → повтор). Печатает: куда ведёт
.ru-редирект, грузится ли реальная выдача .com (а не заглушка), какая валюта,
есть ли цены и островок данных runParams.

    python ali_browser_probe.py "drill bosch"
"""
import re
import sys
import time

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
STEALTH = ("() => {"
           "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
           "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru','en']});"
           "window.chrome={runtime:{}};}")
LAUNCH_ARGS = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]

CUR = {"тенге ₸": "\u20b8", "рубль ₽": "\u20bd", "доллар $": "$",
       "юань ¥": "\u00a5", "евро €": "\u20ac"}
BLOCK_WORDS = ("captcha", "punish", "robot", "verify", "slider", "доступ ограничен",
               "are you a human", "blocked")


def analyze(html):
    cur = {n: html.count(s) for n, s in CUR.items() if html.count(s) > 0}
    prices = re.findall(r"[\u20b8\u20bd\u00a5\u20ac]\s*(\d[\d.,\u00a0\u2009]{1,})", html)
    usd = re.findall(r"(?:US\s*\$|\$)\s*(\d[\d.,]{1,})", html)
    island = [k for k in ("runParams", "_init_data_", '"priceModule"', '"mod"', "window.runParams")
              if k in html]
    return cur, [p.strip()[:12] for p in prices[:6]], usd[:6], island


def run(query):
    api = []

    def on_resp(resp):
        u = resp.url
        if "aliexpress" in u and ("mtop" in u or "/api/" in u or "search" in u):
            api.append((resp.status, u[:80]))

    pw = sync_playwright().start()
    br = pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
    ctx = br.new_context(user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow")
    ctx.add_init_script(STEALTH)
    pg = ctx.new_page()
    pg.on("response", on_resp)

    # 1) куда ведёт .ru (геопроверка)
    ru_final = "?"
    try:
        pg.goto("https://aliexpress.ru/wholesale?SearchText=" + query.replace(" ", "+"),
                wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)
        ru_final = pg.url
    except Exception as e:
        ru_final = "ошибка: " + str(e)[:50]

    # 2) прогрев .com
    try:
        pg.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        pg.evaluate("window.scrollBy(0,500)")
        time.sleep(2)
    except Exception:
        pass

    # 3) поиск на .com с повтором
    url = "https://www.aliexpress.com/w/wholesale-" + query.replace(" ", "-") + ".html"
    title = ""
    html = ""
    for _ in range(8):
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        time.sleep(3)
        try:
            pg.evaluate("window.scrollBy(0,800)")
        except Exception:
            pass
        time.sleep(2)
        try:
            title = pg.title()
            html = pg.content()
        except Exception:
            pass
        if len(html) > 100000 or any(st == 200 for st, _ in api):
            break

    cur, prices, usd, island = analyze(html or "")
    low = (html or "").lower()
    print("\n================ РЕЗУЛЬТАТ ALIEXPRESS (браузер) ================")
    print(".ru редирект увёл на:", ru_final[:70])
    print(".com заголовок:", (title or "")[:70])
    print(".com размер HTML:", len(html or ""), "(реальная выдача обычно >300000)")
    print("ответов от api aliexpress:", len(api), "| статусы:", sorted({s for s, _ in api}))
    print("валюты в HTML:", cur or "—")
    print("цены у символов:", prices or "—", "| $-цены:", usd or "—")
    print("островок данных:", island or "нет")
    print("признак капчи/блока:", any(w in low for w in BLOCK_WORDS))

    ok = (len(html or "") > 100000) and (bool(prices) or bool(usd) or bool(island))
    print("\n=> ВЕРДИКТ:", end=" ")
    if ok:
        print("браузер пробил .com — выдача реальная. Смотри валюту выше (с US-IP вероятно USD).")
    else:
        print("ЗАБЛОКИРОВАНЫ/заглушка. Браузер не дал реальной выдачи с серверного IP — нужен CIS/резид. прокси.")

    br.close()
    pw.stop()


if __name__ == "__main__":
    if sync_playwright is None:
        print("playwright не установлен")
    else:
        run(sys.argv[1] if len(sys.argv) > 1 else "drill bosch")
