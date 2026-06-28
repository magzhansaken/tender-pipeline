#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_price.py — цена Wildberries по ГОТОВОЙ ссылке, СТРОГО по нашему артикулу.

Открывает поиск по артикулу stealth-браузером (Москва, ru-RU, прогрев на
главной), ждёт прохождения антибота WB (с повтором — антибот пускает не
всегда), ловит ответы u-card/cards/{detail,list} и берёт price.product у
товара, чей id == наш артикул. Цену рекомендаций НЕ берёт.

CLI:
    python wb_price.py "https://www.wildberries.ru/catalog/399340573/detail.aspx"
    python wb_price.py --from-db 8     # тест на реальных WB-ссылках из базы
    python wb_price.py --selftest      # мгновенная проверка логики (без браузера)

Зависимости: playwright (+chromium); для --from-db ещё asyncpg.
"""
import os
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


def article_from_url(url):
    u = url or ""
    for pat in (r"/catalog/(\d{4,})/detail", r"/catalog/(\d{6,})", r"/product/[^/]*?(\d{6,})"):
        m = re.search(pat, u)
        if m:
            return int(m.group(1))
    return None


def extract_price_for_article(bodies, art_int):
    """Цена (₽) товара СТРОГО с нашим артикулом. Несём ближайший id сверху;
    на price.product фиксируем (id -> цена). Возврат: мин. цена нашего id или None."""
    hits = []

    def walk(o, near_id):
        if isinstance(o, dict):
            for idk in ("id", "nmId", "nm", "root"):
                v = o.get(idk)
                if isinstance(v, int) and v > 1000:
                    near_id = v
                    break
            pr = o.get("price")
            if isinstance(pr, dict) and isinstance(pr.get("product"), (int, float)):
                hits.append((near_id, round(pr["product"] / 100)))
            for v in o.values():
                walk(v, near_id)
        elif isinstance(o, list):
            for v in o:
                walk(v, near_id)

    for b in bodies:
        walk(b, None)

    ours = [p for nid, p in hits if nid == art_int]
    return min(ours) if ours else None


class WBPriceFetcher:
    def __init__(self, headless=True):
        self.headless = headless
        self.pw = None
        self.br = None

    def _stop(self):
        try:
            if self.br:
                self.br.close()
            if self.pw:
                self.pw.stop()
        except Exception:
            pass
        self.br = None
        self.pw = None

    def _one_attempt(self, article):
        """Один заход: свежий браузер (свежий бросок антибота). Возврат (price|None, captured_bodies_count, title)."""
        bodies = []

        def on_resp(resp):
            u = resp.url
            if "u-card/cards/" in u and ("detail" in u or "list" in u):
                try:
                    bodies.append(resp.json())
                except Exception:
                    pass

        self.pw = sync_playwright().start()
        self.br = self.pw.chromium.launch(headless=self.headless, args=LAUNCH_ARGS)
        ctx = self.br.new_context(user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
                geolocation={"latitude": 55.7558, "longitude": 37.6173}, permissions=["geolocation"])
        ctx.add_init_script(STEALTH)
        pg = ctx.new_page()
        pg.on("response", on_resp)
        title = ""
        try:
            pg.goto("https://www.wildberries.ru/", wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            pg.goto("https://www.wildberries.ru/catalog/0/search.aspx?search=" + str(article),
                    wait_until="domcontentloaded", timeout=60000)
            for _ in range(12):                 # до ~24с; при пройденном антиботе тела приходят за пару сек
                time.sleep(2)
                try:
                    pg.evaluate("window.scrollBy(0, 600)")
                except Exception:
                    pass
                try:
                    title = pg.title()
                except Exception:
                    title = ""
                if bodies:
                    break
        except Exception:
            pass
        price = extract_price_for_article(bodies, int(article)) if bodies else None
        self._stop()
        return price, len(bodies), title

    def fetch(self, url, attempts=3):
        """Цена по ссылке с повтором при антиботе. Возврат (price_rub|None, note)."""
        article = article_from_url(url)
        if not article:
            return None, "нет артикула в ссылке"
        ever_bodies = False
        for a in range(1, attempts + 1):
            price, nbodies, title = self._one_attempt(article)
            if nbodies:
                ever_bodies = True
            if price:
                return price, f"ок (попытка {a})"
            if nbodies and "готов" not in (title or "").lower():
                # страница загрузилась, но нашего артикула на ней нет — не рекомендации брать
                return None, f"товара нет на странице (попытка {a})"
            # иначе антибот не пустил — пробуем снова
        return None, ("антибот не пустил за %d попыток" % attempts) if not ever_bodies \
            else "цена не найдена"


def run_from_db(n):
    import asyncio
    import asyncpg
    db = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")

    async def go():
        conn = await asyncpg.connect(db)
        rows = await conn.fetch(
            "SELECT COALESCE(found_url, match_result->>'source_url') AS url, "
            "       match_result->>'product_name' AS prod FROM tenders "
            "WHERE match_status IN ('FOUND_EXACT','FOUND_PARTIAL') "
            "  AND COALESCE(found_url, match_result->>'source_url') ILIKE '%wildberries.ru/catalog/%' "
            "ORDER BY collected_at DESC LIMIT $1", n)
        await conn.close()
        return rows

    rows = asyncio.run(go())
    if not rows:
        print("В базе нет ссылок wildberries.ru/catalog/.")
        return
    print(f"Проверяю {len(rows)} WB-ссылок (цена строго по артикулу, ₽):\n")
    f = WBPriceFetcher(headless=True)
    ok = 0
    try:
        for i, r in enumerate(rows, 1):
            t = time.time()
            price, note = f.fetch(r["url"])
            dt = time.time() - t
            if price:
                ok += 1
            shown = f"{price:,} ₽".replace(",", " ") if price else "—"
            print(f"  [{i}/{len(rows)}] {shown:>11}  ({dt:.0f}с, {note})  {(r['prod'] or '')[:30]}")
            print(f"        {(r['url'] or '')[:78]}")
    finally:
        f._stop()
    print(f"\nИтог: цена достана у {ok} из {len(rows)}.")


def _selftest():
    art = 399340573
    real_like = [
        {"products": [{"id": 399340573, "sizes": [{"optionId": 1, "price": {"basic": 277100, "product": 147600}}]}]},
        {"products": [{"id": 399340573, "sizes": [{"optionId": 1, "price": {"basic": 277100, "product": 147600}}]}]},
    ]
    assert extract_price_for_article(real_like, art) == 1476
    with_recs = [{"products": [
        {"id": 399340573, "sizes": [{"price": {"product": 147600}}]},
        {"id": 111111111, "sizes": [{"price": {"product": 96300}}]},
        {"id": 222222222, "sizes": [{"price": {"product": 250000}}]}]}]
    assert extract_price_for_article(with_recs, art) == 1476
    only_recs = [{"products": [
        {"id": 111111111, "sizes": [{"price": {"product": 96300}}]},
        {"id": 222222222, "sizes": [{"price": {"product": 250000}}]}]}]
    assert extract_price_for_article(only_recs, art) is None
    multi = [{"products": [{"id": 399340573, "sizes": [
        {"price": {"product": 160000}}, {"price": {"product": 147600}}]}]}]
    assert extract_price_for_article(multi, art) == 1476
    print("selftest OK: наш=1476; рекомендации отсеяны; нет товара->None; мин. размер. Все 4 теста прошли.")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        _selftest()
        return
    if sync_playwright is None:
        print("playwright не установлен. pip install playwright && playwright install chromium")
        return
    if args and args[0] == "--from-db":
        run_from_db(int(args[1]) if len(args) > 1 else 8)
        return
    if not args:
        print(__doc__)
        return
    f = WBPriceFetcher(headless=True)
    try:
        t = time.time()
        price, note = f.fetch(args[0])
        dt = time.time() - t
        print(f"\nЦЕНА: {price:,} ₽".replace(",", " ") + f"  ({dt:.0f}с, {note})" if price
              else f"\nЦена не получена ({note})")
    finally:
        f._stop()


if __name__ == "__main__":
    main()
