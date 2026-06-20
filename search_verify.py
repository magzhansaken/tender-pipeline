"""
Этап 4 (поиск) + Этап 5 (сверка).

Берёт тендеры со стадией 'parsed' (есть анкета structured_spec), ищет товар на
маркетплейсах через DDGS (бесплатный DuckDuckGo), затем сверяет найденных кандидатов
с анкетой через Ollama. Результат пишет в базу, ставит стадию 'searched'.

Запуск (ключ из .env):
    OLLAMA_API_KEY=... python search_verify.py

Переменные окружения:
    OLLAMA_API_KEY   - ключ Ollama Cloud (из .env)
    OLLAMA_MODEL     - модель (по умолчанию gpt-oss:20b)
    DATABASE_URL     - подключение к базе
    LIMIT            - обработать не больше N (0 = все; для теста LIMIT=5)
    SITES            - список сайтов через запятую (по умолчанию kaspi,satu,ozon,wb,yandex,alibaba,1688)
    DDGS_WORKERS     - параллельных поисков (по умолчанию 6)
    DDGS_PAUSE       - пауза между поисками, сек (по умолчанию 0.4)
    DDGS_MAX_RESULTS - результатов на сайт (по умолчанию 3)
    DELAY            - пауза между лотами, сек (по умолчанию 1.0)

Возобновляемый: берёт только stage='parsed'. Успех -> 'searched'. Сбой -> 'search_error'.
"""
import os
import sys
import json
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import asyncpg
from ollama import Client

# Гибрид: после сверки берём реальную цену со страницы товара.
# Мягкий импорт — если модуля/requests нет, просто не дёргаем цену (без падений).
try:
    from price_fetch import fetch_price
except Exception:
    fetch_price = None

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
LIMIT = int(os.getenv("LIMIT", "0"))
DELAY = float(os.getenv("DELAY", "1.0"))

SITES_DEFAULT = "kaspi.kz,satu.kz,chipdip.kz,otvertka.kz,ozon.ru,wildberries.ru,market.yandex.ru,alibaba.com,1688.com"
SITES = [s.strip() for s in os.getenv("SITES", SITES_DEFAULT).split(",") if s.strip()]
DDGS_WORKERS = int(os.getenv("DDGS_WORKERS", "6"))
DDGS_PAUSE = float(os.getenv("DDGS_PAUSE", "0.4"))
DDGS_MAX_RESULTS = int(os.getenv("DDGS_MAX_RESULTS", "3"))
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "8"))

VERIFY_PROMPT = (
    "Ты — система верификации товаров для госзакупок Казахстана.\n"
    "Тебе дают ТРЕБОВАНИЯ из ТЗ и список КАНДИДАТОВ с маркетплейсов. "
    "Определи, какой кандидат подходит под ТЗ.\n\n"
    "ПРАВИЛА:\n"
    "1) Бренд и модель бери ТОЛЬКО из данных кандидата (title/snippet). НЕ ПРИДУМЫВАЙ.\n"
    "2) Учитывай пороги характеристик: \">=\" значит не менее, \"<=\" не более, \"==\" точно. "
    "Если кандидат нарушает порог (нужно не менее 16, а у него 8) — это conflict, а не совпадение.\n"
    "3) matched_specs — только то, что реально подтверждается текстом кандидата. Не уверен — не пиши.\n"
    "4) Если ни один кандидат не подходит — status: NOT_FOUND.\n"
    "5) source_url и source_site бери у выбранного кандидата.\n"
    "6) Если ссылка кандидата ведёт на КАТЕГОРИЮ, поиск или список товаров "
    "(например .../catalog/tags/..., .../search..., .../category...), а не на конкретную "
    "карточку товара — это НЕ найденный товар, не выбирай его.\n"
    "7) confidence обязан соответствовать статусу: FOUND_EXACT = 70-100, "
    "FOUND_PARTIAL = 40-69, NOT_FOUND = 0-30. НЕ ставь 0 при FOUND_EXACT или FOUND_PARTIAL.\n"
    "8) price: если в title или snippet выбранного кандидата ЯВНО видна цена — верни её "
    "целым числом без пробелов, точек и символов валюты (например 355000). "
    "Если цены не видно — null. ЦЕНУ НЕ ВЫДУМЫВАЙ.\n\n"
    "ТОЛЬКО JSON, без markdown и без текста вокруг:\n"
    "{\n"
    '  "status": "FOUND_EXACT / FOUND_PARTIAL / NOT_FOUND",\n'
    '  "brand": "бренд или null",\n'
    '  "model": "модель или null",\n'
    '  "product_name": "название найденного товара",\n'
    '  "price": число или null,\n'
    '  "source_url": "ссылка кандидата",\n'
    '  "source_site": "kaspi.kz / ozon.ru / ...",\n'
    '  "matched_specs": ["..."],\n'
    '  "missing_specs": ["..."],\n'
    '  "conflicts": ["..."],\n'
    '  "confidence": 0,\n'
    '  "reason": "кратко почему"\n'
    "}"
)


def load_anketa(val):
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except Exception:
        return {}


def build_queries(anketa: dict):
    """1-2 коротких поисковых запроса из анкеты."""
    queries = []
    sq = (anketa.get("search_query") or "").strip()
    if sq:
        queries.append(sq)
    pt = (anketa.get("product_type") or "").strip()
    brand = anketa.get("brand")
    alt = " ".join(x for x in [pt, (brand or "").strip()] if x).strip()
    if alt and alt.lower() not in [q.lower() for q in queries]:
        queries.append(alt)
    return queries[:2] or ([pt] if pt else [])


def ddgs_search(queries):
    """Поиск по всем сайтам через DuckDuckGo. Возвращает уникальных кандидатов."""
    from ddgs import DDGS

    results_all = []
    lock = threading.Lock()
    tasks = [(q, site) for q in queries for site in SITES]

    def search_one(args):
        query, site = args
        full_q = f"site:{site} {query}"
        time.sleep(DDGS_PAUSE)
        try:
            res = DDGS().text(full_q, region="ru-ru", max_results=DDGS_MAX_RESULTS)
            if res:
                with lock:
                    for r in res:
                        results_all.append({
                            "title": r.get("title", ""),
                            "url": r.get("href", ""),
                            "snippet": r.get("body", ""),
                            "site": site,
                        })
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=DDGS_WORKERS) as ex:
        list(ex.map(search_one, tasks))

    seen = set()
    unique = []
    for r in results_all:
        if r["url"] and r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    return unique


LISTING_MARKERS = ("/catalog/tags", "/search", "/category", "/categories", "/promo", "/brands/", "?search", "/tags/", "/cat/")
PRODUCT_MARKERS = ("/shop/p/", "/product/", "/item/", "/goods/", "/detail", "/p/")


def rank_candidates(candidates, queries):
    """Сортирует кандидатов: больше совпавших слов запроса = выше; карточки товара выше списков."""
    qwords = set()
    for q in queries:
        for w in q.lower().split():
            if len(w) >= 3:
                qwords.add(w)

    def score(c):
        text = f"{c.get('title', '')} {c.get('snippet', '')}".lower()
        s = sum(1 for w in qwords if w in text)
        url = (c.get("url") or "").lower()
        if any(m in url for m in LISTING_MARKERS):
            s -= 5
        if any(m in url for m in PRODUCT_MARKERS):
            s += 2
        return s

    return sorted(candidates, key=score, reverse=True)


def verify(client, anketa, candidates):
    """Сверка кандидатов с анкетой через Ollama -> вердикт (dict)."""
    cand_text = ""
    for i, c in enumerate(candidates[:MAX_CANDIDATES], 1):
        cand_text += (
            f"\nКандидат #{i} ({c['site']}):\n"
            f"  Title: {c['title']}\n"
            f"  URL: {c['url']}\n"
            f"  Snippet: {c['snippet'][:250]}\n"
        )

    attrs = anketa.get("attributes", []) or []
    attrs_text = "; ".join(
        f"{a.get('name', '')} {a.get('op', '')} {a.get('value', '')} {a.get('unit', '')}".strip()
        for a in attrs
    ) or "не заданы"

    user = (
        "ТРЕБОВАНИЯ ИЗ ТЗ:\n"
        f"Тип товара: {anketa.get('product_type', '?')}\n"
        f"Нужен конкретный бренд: {anketa.get('brand_required', False)}\n"
        f"Бренд из ТЗ: {anketa.get('brand') or 'не указан'}\n"
        f"Модель из ТЗ: {anketa.get('model') or 'не указана'}\n"
        f"Характеристики (с порогами): {attrs_text}\n\n"
        f"КАНДИДАТЫ С МАРКЕТПЛЕЙСОВ:\n{cand_text}\n\n"
        "Сравни кандидатов с требованиями ТЗ. Помни про пороги (>= не менее, <= не более, == точно). ТОЛЬКО JSON."
    )

    resp = client.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": VERIFY_PROMPT},
            {"role": "user", "content": user},
        ],
        stream=False,
        think=False,
    )
    raw = (resp["message"]["content"] or "").strip()
    if "```" in raw:
        raw = "\n".join(l for l in raw.split("\n") if not l.strip().startswith("```")).strip()
    s, e = raw.find("{"), raw.rfind("}") + 1
    if s < 0 or e <= s:
        raise ValueError("JSON не найден в ответе модели")
    result = json.loads(raw[s:e])

    # Анти-галлюцинация: найденный бренд должен встречаться в тексте кандидатов
    brand = result.get("brand")
    if brand:
        haystack = " ".join(f"{c['title']} {c['snippet']}" for c in candidates).lower()
        if str(brand).lower() not in haystack:
            result["brand"] = None
            result["_brand_warning"] = f"бренд '{brand}' не найден в кандидатах"

    return result


async def main():
    if not OLLAMA_API_KEY:
        print("❌ Не задан OLLAMA_API_KEY (положи ключ в .env). Воркер не запущен.")
        sys.exit(1)

    client = Client(
        host="https://ollama.com",
        headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
    )

    conn = await asyncpg.connect(DATABASE_URL)

    # колонки под результат поиска/сверки (создаём, если ещё нет)
    await conn.execute(
        "ALTER TABLE tenders ADD COLUMN IF NOT EXISTS match_result jsonb;"
        "ALTER TABLE tenders ADD COLUMN IF NOT EXISTS match_status text;"
        "ALTER TABLE tenders ADD COLUMN IF NOT EXISTS found_url text;"
        "ALTER TABLE tenders ADD COLUMN IF NOT EXISTS confidence int;"
    )

    sel = (
        "SELECT id, lot_number, name, structured_spec FROM tenders "
        "WHERE stage = 'parsed' ORDER BY collected_at"
    )
    if LIMIT > 0:
        sel += f" LIMIT {LIMIT}"

    rows = await conn.fetch(sel)
    total = len(rows)
    print(f"К поиску: {total} тендеров | сайты: {', '.join(SITES)} | модель: {OLLAMA_MODEL}")
    if total == 0:
        await conn.close()
        print("Нет тендеров со стадией 'parsed'.")
        return

    found = 0
    notfound = 0
    err = 0
    for i, r in enumerate(rows, 1):
        anketa = load_anketa(r["structured_spec"])
        queries = build_queries(anketa)
        ptype = str(anketa.get("product_type", r["name"]))[:28]

        if not queries:
            await conn.execute("UPDATE tenders SET match_status='NO_QUERY', stage='searched' WHERE id=$1", r["id"])
            notfound += 1
            print(f"[{i}/{total}] ⚠️  {r['lot_number']}: {ptype} | нет поискового запроса")
            await asyncio.sleep(DELAY)
            continue

        try:
            candidates = await asyncio.to_thread(ddgs_search, queries)
        except Exception as e:
            candidates = []

        candidates = rank_candidates(candidates, queries)

        if not candidates:
            result = {"status": "NOT_FOUND", "reason": "поиск не дал кандидатов", "confidence": 0}
        else:
            try:
                result = await asyncio.to_thread(verify, client, anketa, candidates)
            except Exception as e:
                err += 1
                await conn.execute("UPDATE tenders SET match_status='search_error', stage='search_error' WHERE id=$1", r["id"])
                print(f"[{i}/{total}] ❌ {r['lot_number']}: {ptype} | сверка: {str(e)[:80]}")
                await asyncio.sleep(DELAY)
                continue

        status = result.get("status", "NOT_FOUND")
        conf = result.get("confidence", 0)
        try:
            conf = int(conf)
        except Exception:
            conf = 0

        # ГИБРИД: товар найден сверкой -> идём на его страницу и берём реальную цену.
        # fetch_price сам молча вернёт None для неподдержанных площадок и для
        # ссылок-категорий, поэтому лишних запросов и фальшивых цен не будет.
        if fetch_price and status in ("FOUND_EXACT", "FOUND_PARTIAL"):
            src = result.get("source_url")
            if src:
                try:
                    price, _note = await asyncio.to_thread(fetch_price, src)
                    if price:
                        result["price"] = price
                except Exception:
                    pass

        await conn.execute(
            "UPDATE tenders SET match_result=$1::jsonb, match_status=$2, found_url=$3, confidence=$4, stage='searched' WHERE id=$5",
            json.dumps(result, ensure_ascii=False),
            status,
            result.get("source_url"),
            conf,
            r["id"],
        )

        if status in ("FOUND_EXACT", "FOUND_PARTIAL"):
            found += 1
            icon = "✅" if status == "FOUND_EXACT" else "🟡"
            site = result.get("source_site", "") or ""
            print(f"[{i}/{total}] {icon} {r['lot_number']}: {ptype} | {status} | {site} | {conf}% | кандидатов: {len(candidates)}")
        else:
            notfound += 1
            print(f"[{i}/{total}] ❌ {r['lot_number']}: {ptype} | NOT_FOUND | кандидатов: {len(candidates)}")

        await asyncio.sleep(DELAY)

    remaining = await conn.fetchval("SELECT count(*) FROM tenders WHERE stage='parsed'")
    await conn.close()
    print(f"\nГотово: найдено {found}, не найдено {notfound}, ошибок {err}. Осталось 'parsed': {remaining}")


if __name__ == "__main__":
    asyncio.run(main())
