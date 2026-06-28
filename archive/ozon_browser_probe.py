#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ozon_browser_probe.py — РЕШАЮЩИЙ тест: пускает ли Ozon.kz наш серверный IP
в ЖИВОМ браузере (наш WB-рецепт: прогрев главной → поиск → повтор при антиботе).

Печатает по каждой попытке: заголовок, признаки antirobot-блока, сколько
ответов и сколько из них от api.ozon.kz/composer (статусы), и есть ли цены
в тенге в HTML (с тонким пробелом \\u2009). Это диагностика, не парсер.

    python ozon_browser_probe.py "кабель ВВГ 3х2.5"
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
           "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru']});"
           "window.chrome={runtime:{}};}")
LAUNCH_ARGS = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]

PRICE_RE = re.compile(r"(\d[\d\s\u2009\u00a0]{2,})\s*\u20b8")
BLOCK_WORDS = ("antirobot", "доступ ограничен", "вы не робот", "captcha",
               "проверка", "robot", "blocked", "access denied")


def run(query):
    api_hits = []   # (status, short_url)
    bodies_json = []

    def on_resp(resp):
        u = resp.url
        if "ozon.kz" in u and ("composer-api" in u or "entrypoint-api" in u or "/api/" in u):
            api_hits.append((resp.status, u[:90]))
            try:
                if "json" in (resp.headers or {}).get("content-type", ""):
                    bodies_json.append(resp.text())
            except Exception:
                pass

    pw = sync_playwright().start()
    br = pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
    ctx = br.new_context(user_agent=UA, locale="ru-RU", timezone_id="Asia/Almaty",
            geolocation={"latitude": 43.2220, "longitude": 76.8512}, permissions=["geolocation"])
    ctx.add_init_script(STEALTH)
    pg = ctx.new_page()
    pg.on("response", on_resp)

    print(">>> прогрев главной ozon.kz ...")
    try:
        pg.goto("https://www.ozon.kz/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        pg.evaluate("window.scrollBy(0, 500)")
        time.sleep(2)
    except Exception as e:
        print("   главная:", str(e)[:70])

    search_url = "https://www.ozon.kz/search/?text=" + query.replace(" ", "+")
    print(">>> поиск:", search_url)
    title = ""
    for _ in range(12):
        try:
            pg.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        time.sleep(3)
        try:
            pg.evaluate("window.scrollBy(0, 600)")
        except Exception:
            pass
        time.sleep(2)
        try:
            title = pg.title()
        except Exception:
            title = ""
        if any(st == 200 for st, _ in api_hits):
            break

    html = ""
    try:
        html = pg.content()
    except Exception:
        pass

    print("\n================ РЕЗУЛЬТАТ OZON.KZ (браузер) ================")
    print("Заголовок страницы:", (title or "")[:80])
    low = (html or "").lower()
    blocked = any(w in low for w in BLOCK_WORDS) or (title or "").strip().lower() in ("", "ozon")
    statuses = [st for st, _ in api_hits]
    got200 = any(s == 200 for s in statuses)
    print("Ответов от api ozon.kz:", len(api_hits), "| статусы:", sorted(set(statuses)))
    for st, u in api_hits[:8]:
        print("   [%s] %s" % (st, u))
    prices = []
    for m in PRICE_RE.findall(html or ""):
        clean = re.sub(r"[\s\u2009\u00a0]", "", m)
        if clean.isdigit() and 100 < int(clean) < 50000000:
            prices.append(int(clean))
    print("Цены в тенге в HTML (первые 8):", sorted(set(prices))[:8])
    print("Признак antirobot-блока в HTML:", any(w in low for w in BLOCK_WORDS))

    print("\n=> ВЕРДИКТ:", end=" ")
    if got200 or prices:
        print("ПРОШЛИ! Ozon.kz отвечает браузеру с серверного IP — Ozon KZ реально сделать (тенге).")
    else:
        print("ЗАБЛОКИРОВАНЫ. Браузер не пробил Ozon с серверного IP — нужен резидентный прокси.")

    br.close()
    pw.stop()


if __name__ == "__main__":
    if sync_playwright is None:
        print("playwright не установлен")
    else:
        run(sys.argv[1] if len(sys.argv) > 1 else "кабель ВВГ 3х2.5")
