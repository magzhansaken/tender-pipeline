"""
ЛАБОРАТОРИЯ ПРОМПТОВ / МОДЕЛЕЙ.

Гоняет ЗАМОРОЖЕННЫЙ золотой набор (golden_set.json) через все этапы
(анкета -> поиск -> сверка) с ЗАДАННОЙ моделью / температурой / think / промптом
и сохраняет ВСЁ в один файл lab_<tag>.json для анализа.

Боевую базу НЕ трогает (читает только golden_set.json, пишет только в файл).

ПРИНЦИП: один и тот же набор + меняем ОДНУ настройку за раз = честное сравнение.

Настройки (env):
  LAB_MODEL         модель (default gpt-oss:20b; попробуй gpt-oss:120b)
  LAB_TEMPERATURE   температура (default 0 — стабильно и воспроизводимо)
  LAB_THINK         gpt-oss: low / medium / high (или false). default low
  LAB_TAG           метка для имени файла (например base20b, big120b, normV2)
  NORM_PROMPT_VER   версия промпта нормализации (default v1)
  VERIFY_PROMPT_VER версия промпта сверки (default v1)
  GOLDEN_FILE       файл набора (default golden_set.json)
  OLLAMA_API_KEY    ключ Ollama (из .env)

    OLLAMA_API_KEY=... LAB_MODEL=gpt-oss:20b LAB_TAG=base20b python lab.py
"""
import os
import sys
import json
import time
import asyncio
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from ollama import Client

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
LAB_MODEL = os.getenv("LAB_MODEL", "gpt-oss:20b")
LAB_TEMPERATURE = float(os.getenv("LAB_TEMPERATURE", "0"))
LAB_THINK_RAW = os.getenv("LAB_THINK", "low")
LAB_TAG = os.getenv("LAB_TAG", "run")
NORM_VER = os.getenv("NORM_PROMPT_VER", "v1")
VERIFY_VER = os.getenv("VERIFY_PROMPT_VER", "v1")
GOLDEN_FILE = os.getenv("GOLDEN_FILE", "golden_set.json")

# think: строка-уровень для gpt-oss, либо False
if LAB_THINK_RAW.strip().lower() in ("false", "none", "off", ""):
    THINK = False
else:
    THINK = LAB_THINK_RAW.strip().lower()

OPTIONS = {"temperature": LAB_TEMPERATURE, "seed": 42}

# ---- параметры поиска (ПРОВЕРЕННЫЕ, как в боевом search_verify — НЕ разгоняем) ----
SITES = [s.strip() for s in os.getenv(
    "SITES", "kaspi.kz,satu.kz,ozon.ru,wildberries.ru,market.yandex.ru,alibaba.com,1688.com"
).split(",") if s.strip()]
DDGS_WORKERS = int(os.getenv("DDGS_WORKERS", "6"))
DDGS_PAUSE = float(os.getenv("DDGS_PAUSE", "0.4"))
DDGS_MAX_RESULTS = int(os.getenv("DDGS_MAX_RESULTS", "3"))
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "8"))

LISTING_MARKERS = ("/catalog/tags", "/search", "/category", "/categories", "/promo", "/brands/", "?search", "/tags/", "/cat/")
PRODUCT_MARKERS = ("/shop/p/", "/product/", "/item/", "/goods/", "/detail", "/p/")

# ════════════════════ ПРОМПТЫ (версии) ════════════════════

NORMALIZE_PROMPTS = {
    "v1": (
        "Ты — система нормализации технических заданий (ТЗ) госзакупок Казахстана.\n"
        "На входе сырой текст ТЗ одного лота. Верни СТРОГО JSON по схеме ниже — "
        "без пояснений, без markdown, без текста до или после JSON.\n\n"
        "ПРАВИЛА:\n"
        "1) НЕ ПРИДУМЫВАЙ бренд и модель. Указывай brand/model ТОЛЬКО если они явно "
        "написаны в тексте ТЗ. Если их нет — ставь null.\n"
        "2) Для каждого параметра определи оператор сравнения op по словам в тексте:\n"
        '   "=="  — точное значение (нет слов "не менее/не более", просто значение);\n'
        '   ">="  — "не менее", "не ниже", "от", "минимум";\n'
        '   "<="  — "не более", "не выше", "до", "максимум";\n'
        '   "~"   — "примерно", "около", "порядка".\n'
        "3) value — само число или значение; unit — единица измерения (ГБ, мм, Вт, шт...) "
        "или пустая строка, если единицы нет.\n"
        "4) search_query — короткая строка, как ты сам искал бы этот товар в интернет-"
        "магазине (тип товара + ключевые параметры, и бренд если он есть).\n\n"
        "СХЕМА:\n"
        "{\n"
        '  "product_type": "<что за товар, кратко>",\n'
        '  "brand_required": <true или false: требует ли ТЗ конкретный бренд>,\n'
        '  "brand": <бренд из текста или null>,\n'
        '  "model": <модель из текста или null>,\n'
        '  "attributes": [\n'
        '    {"name": "<параметр>", "value": "<значение>", "unit": "<ед. или пусто>", "op": "==|>=|<=|~"}\n'
        "  ],\n"
        '  "search_query": "<строка для поиска товара>"\n'
        "}"
    ),
}

VERIFY_PROMPTS = {
    "v1": (
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
        "FOUND_PARTIAL = 40-69, NOT_FOUND = 0-30. НЕ ставь 0 при FOUND_EXACT или FOUND_PARTIAL.\n\n"
        "ТОЛЬКО JSON, без markdown и без текста вокруг:\n"
        "{\n"
        '  "status": "FOUND_EXACT / FOUND_PARTIAL / NOT_FOUND",\n'
        '  "brand": "бренд или null",\n'
        '  "model": "модель или null",\n'
        '  "product_name": "название найденного товара",\n'
        '  "source_url": "ссылка кандидата",\n'
        '  "source_site": "kaspi.kz / ozon.ru / ...",\n'
        '  "matched_specs": ["..."],\n'
        '  "missing_specs": ["..."],\n'
        '  "conflicts": ["..."],\n'
        '  "confidence": 0,\n'
        '  "reason": "кратко почему"\n'
        "}"
    ),
}

# ════════════════════ ОБЩИЕ ФУНКЦИИ ════════════════════


def extract_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if "```" in raw:
        raw = "\n".join(ln for ln in raw.split("\n") if not ln.strip().startswith("```")).strip()
    s, e = raw.find("{"), raw.rfind("}") + 1
    if s < 0 or e <= s:
        raise ValueError("JSON не найден в ответе модели")
    return json.loads(raw[s:e])


def normalize(client, raw_spec, prompt):
    resp = client.chat(
        model=LAB_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Текст ТЗ:\n{raw_spec[:6000]}\n\nВерни JSON по схеме."},
        ],
        stream=False,
        think=THINK,
        options=OPTIONS,
    )
    data = extract_json(resp["message"]["content"])
    brand = data.get("brand")
    if brand and str(brand).lower() not in raw_spec.lower():
        data["_brand_warning"] = f"бренд '{brand}' выдуман — в ТЗ его нет"
        data["brand"] = None
        data["brand_required"] = False
    return data


def build_queries(anketa):
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
    from ddgs import DDGS
    out = []
    lock = threading.Lock()
    tasks = [(q, site) for q in queries for site in SITES]

    def one(args):
        query, site = args
        time.sleep(DDGS_PAUSE)
        try:
            res = DDGS().text(f"site:{site} {query}", region="ru-ru", max_results=DDGS_MAX_RESULTS)
            if res:
                with lock:
                    for r in res:
                        out.append({"title": r.get("title", ""), "url": r.get("href", ""),
                                    "snippet": r.get("body", ""), "site": site})
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=DDGS_WORKERS) as ex:
        list(ex.map(one, tasks))

    seen, uniq = set(), []
    for r in out:
        if r["url"] and r["url"] not in seen:
            seen.add(r["url"])
            uniq.append(r)
    return uniq


def rank_candidates(candidates, queries):
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


def verify(client, anketa, candidates, prompt):
    cand_text = ""
    for i, c in enumerate(candidates[:MAX_CANDIDATES], 1):
        cand_text += (f"\nКандидат #{i} ({c['site']}):\n  Title: {c['title']}\n"
                      f"  URL: {c['url']}\n  Snippet: {c['snippet'][:250]}\n")
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
        "Сравни кандидатов с требованиями ТЗ. Помни про пороги. ТОЛЬКО JSON."
    )
    resp = client.chat(
        model=LAB_MODEL,
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": user}],
        stream=False,
        think=THINK,
        options=OPTIONS,
    )
    result = extract_json(resp["message"]["content"])
    brand = result.get("brand")
    if brand:
        hay = " ".join(f"{c['title']} {c['snippet']}" for c in candidates).lower()
        if str(brand).lower() not in hay:
            result["brand"] = None
            result["_brand_warning"] = f"бренд '{brand}' не найден в кандидатах"
    return result


# ════════════════════ ПРОГОН ════════════════════


async def main():
    if not OLLAMA_API_KEY:
        print("❌ Не задан OLLAMA_API_KEY.")
        sys.exit(1)
    if NORM_VER not in NORMALIZE_PROMPTS:
        print(f"❌ Нет промпта нормализации версии '{NORM_VER}'. Есть: {list(NORMALIZE_PROMPTS)}")
        sys.exit(1)
    if VERIFY_VER not in VERIFY_PROMPTS:
        print(f"❌ Нет промпта сверки версии '{VERIFY_VER}'. Есть: {list(VERIFY_PROMPTS)}")
        sys.exit(1)
    if not os.path.exists(GOLDEN_FILE):
        print(f"❌ Нет файла набора {GOLDEN_FILE}. Сначала запусти build_golden.py")
        sys.exit(1)

    with open(GOLDEN_FILE, encoding="utf-8") as f:
        golden = json.load(f)

    lab_limit = int(os.getenv("LAB_LIMIT", "0"))
    if lab_limit > 0:
        golden = golden[:lab_limit]

    norm_prompt = NORMALIZE_PROMPTS[NORM_VER]
    verify_prompt = VERIFY_PROMPTS[VERIFY_VER]

    client = Client(host="https://ollama.com", headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"})

    print(f"ЛАБОРАТОРИЯ | модель={LAB_MODEL} | temp={LAB_TEMPERATURE} | think={THINK} | "
          f"промпты norm={NORM_VER}/verify={VERIFY_VER} | набор={len(golden)} лотов")

    results = []
    counts = {"FOUND_EXACT": 0, "FOUND_PARTIAL": 0, "NOT_FOUND": 0, "ERROR": 0}
    conf_sum = 0
    conf_n = 0

    for i, lot in enumerate(golden, 1):
        ln = lot["lot_number"]
        name = lot.get("name", "")
        raw = lot.get("raw_spec", "")
        item = {"lot_number": ln, "name": name, "raw_spec": raw[:3000]}
        t0 = time.time()

        # этап 3 — анкета
        try:
            anketa = await asyncio.to_thread(normalize, client, raw, norm_prompt)
            item["anketa"] = anketa
        except Exception as e:
            item["anketa_error"] = str(e)[:200]
            item["verdict"] = {"status": "ERROR", "reason": "normalize failed"}
            counts["ERROR"] += 1
            results.append(item)
            print(f"[{i}/{len(golden)}] ❌ {ln}: ошибка анкеты")
            continue
        t1 = time.time()

        # этап 4 — поиск
        queries = build_queries(anketa)
        try:
            candidates = await asyncio.to_thread(ddgs_search, queries)
        except Exception:
            candidates = []
        candidates = rank_candidates(candidates, queries)
        item["candidates_count"] = len(candidates)
        item["candidates_top"] = [
            {"title": c["title"], "url": c["url"], "site": c["site"]}
            for c in candidates[:MAX_CANDIDATES]
        ]
        t2 = time.time()

        # этап 5 — сверка
        if not candidates:
            verdict = {"status": "NOT_FOUND", "reason": "поиск не дал кандидатов", "confidence": 0}
        else:
            try:
                verdict = await asyncio.to_thread(verify, client, anketa, candidates, verify_prompt)
            except Exception as e:
                verdict = {"status": "ERROR", "reason": f"verify failed: {str(e)[:150]}"}
        item["verdict"] = verdict
        t3 = time.time()

        item["timing_sec"] = {"normalize": round(t1 - t0, 1), "search": round(t2 - t1, 1),
                              "verify": round(t3 - t2, 1), "total": round(t3 - t0, 1)}

        st = verdict.get("status", "ERROR")
        counts[st] = counts.get(st, 0) + 1
        c = verdict.get("confidence")
        if isinstance(c, (int, float)) and st in ("FOUND_EXACT", "FOUND_PARTIAL"):
            conf_sum += c
            conf_n += 1

        results.append(item)
        icon = {"FOUND_EXACT": "✅", "FOUND_PARTIAL": "🟡", "NOT_FOUND": "❌", "ERROR": "⚠️"}.get(st, "?")
        ptype = str(anketa.get("product_type", name))[:26]
        print(f"[{i}/{len(golden)}] {icon} {ln}: {ptype} | {st} | "
              f"{verdict.get('confidence', '-')}% | канд: {len(candidates)}")

    out = {
        "meta": {
            "tag": LAB_TAG,
            "model": LAB_MODEL,
            "temperature": LAB_TEMPERATURE,
            "think": THINK,
            "norm_prompt_ver": NORM_VER,
            "verify_prompt_ver": VERIFY_VER,
            "datetime": datetime.now().isoformat(timespec="seconds"),
            "golden_count": len(golden),
        },
        "summary": {
            **counts,
            "avg_confidence_found": round(conf_sum / conf_n, 1) if conf_n else None,
        },
        "results": results,
    }
    fname = f"lab_{LAB_TAG}.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\n=== ИТОГ ({LAB_TAG}) ===")
    print(f"точных {counts['FOUND_EXACT']}, частичных {counts['FOUND_PARTIAL']}, "
          f"не найдено {counts['NOT_FOUND']}, ошибок {counts['ERROR']}")
    print(f"средняя уверенность (найденных): {out['summary']['avg_confidence_found']}")
    print(f"💾 файл: {fname}")


if __name__ == "__main__":
    asyncio.run(main())
