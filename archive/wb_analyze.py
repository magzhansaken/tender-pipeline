#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Структура ответа WB: для каждой цены показываем, какому артикулу она
принадлежит (несём ближайший id сверху) и путь до неё."""
import sys, time
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

bodies = []

def on_resp(resp):
    u = resp.url
    if "u-card/cards/" in u and ("detail" in u or "list" in u):
        try:
            bodies.append(("detail" if "detail" in u else "list", resp.json()))
        except Exception:
            pass

def walk(o, near_id, near_name, path, out):
    if isinstance(o, dict):
        for idk in ("id", "nmId", "nm", "root"):
            v = o.get(idk)
            if isinstance(v, int) and v > 1000:
                near_id = v
                near_name = str(o.get("name", "") or near_name)[:30]
                break
        pr = o.get("price")
        if isinstance(pr, dict) and isinstance(pr.get("product"), (int, float)):
            out.append((near_id, near_name, pr["product"], path, list(o.keys())[:10]))
        for k, v in o.items():
            walk(v, near_id, near_name, path + "." + str(k), out)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            walk(v, near_id, near_name, path + "[" + str(i) + "]", out)

def collect_all():
    out = []
    for tag, b in bodies:
        walk(b, None, "", tag, out)
    return out

def run(article):
    art_int = int(article)
    pw = sync_playwright().start()
    br = pw.chromium.launch(headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    ctx = br.new_context(user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
            geolocation={"latitude": 55.7558, "longitude": 37.6173}, permissions=["geolocation"])
    ctx.add_init_script(STEALTH)
    pg = ctx.new_page()
    pg.on("response", on_resp)
    pg.goto("https://www.wildberries.ru/", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    pg.goto("https://www.wildberries.ru/catalog/0/search.aspx?search=" + article,
            wait_until="domcontentloaded", timeout=60000)
    title = ""
    waited = 0
    for _ in range(20):
        time.sleep(2)
        waited += 2
        try:
            pg.evaluate("window.scrollBy(0, 600)")
        except Exception:
            pass
        try:
            title = pg.title()
        except Exception:
            title = ""
        if collect_all():
            break
    print("\n================ СТРУКТУРА И ЭТАЛОН WB ================")
    print("Артикул:", article, "| ждали:", waited, "сек | тел поймано:", len(bodies))
    print("Заголовок (настоящая цена тут):\n   ", title)
    rows = collect_all()
    if not rows:
        print("\n!!! Цен не нашли. Если заголовок 'Почти готово' — антибот не прошёл, запусти ещё раз.")
        print("    Если заголовок реальный — пришли его мне, структура иная.")
        br.close(); pw.stop(); return
    print("\nВСЕ цены от WB с привязкой к артикулу:")
    print("  %-12s %9s  %-28s %s" % ("артикул", "цена,руб", "путь", "ключи"))
    seen = set()
    for nid, nm, p, pa, keys in rows:
        kk = (nid, p, pa)
        if kk in seen:
            continue
        seen.add(kk)
        mark = "  <<< НАШ" if nid == art_int else ""
        print("  %-12s %9d  %-28s %s%s" % (nid, round(p / 100), pa[:28], keys, mark))
    our = sorted({round(p / 100) for nid, nm, p, pa, keys in rows if nid == art_int})
    other = sorted({round(p / 100) for nid, nm, p, pa, keys in rows if nid != art_int})
    print("\n=> ЭТАЛОН — цена НАШЕГО артикула %s: %s руб" % (article, our if our else "НЕ найдена"))
    print("=> чужие цены (брать НЕЛЬЗЯ):", other[:10])
    br.close()
    pw.stop()

def _selftest():
    global bodies
    bodies = [
        ("detail", {"data": {"products": [
            {"id": 399340573, "name": "Бумага",
             "sizes": [{"price": {"basic": 277100, "product": 147600}}]}]}}),
        ("list", {"data": {"products": [
            {"id": 399340573, "sizes": [{"price": {"product": 147600}}]},
            {"id": 111111111, "sizes": [{"price": {"product": 96300}}]}]}}),
    ]
    rows = collect_all()
    our = sorted({round(p / 100) for nid, nm, p, pa, k in rows if nid == 399340573})
    assert our == [1476], our
    print("selftest OK: наш=1476, строк:", len(rows))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
    elif sync_playwright is None:
        print("playwright не установлен")
    else:
        run(sys.argv[1] if len(sys.argv) > 1 else "399340573")
