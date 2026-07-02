"""
bench_compare.py — ТЕСТ-СТЕНД: старый промпт нормализации vs новый (карточки+универсальный).

Берёт N реальных тендеров из БД, для каждого прогоняет ОБА системных промпта через
ollama.com и кладёт рядом: search_query, attributes, category. НИЧЕГО не пишет в боевые
таблицы (tenders/lots) — только читает и печатает отчёт + пишет bench_compare.csv.

Это «линейка» из плана (Фаза 2): показывает «было/стало» ДО включения MATCHING_MODE=on.

Запуск НА СЕРВЕРЕ (там модель и ключ):
    cd /opt/tenderview/tender-pipeline/src
    OLLAMA_API_KEY=$(grep OLLAMA_API_KEY /opt/tenderview/.env | cut -d= -f2) \
    DATABASE_URL=postgresql://tender:ПАРОЛЬ@db:5432/tender \
    python3 matching/bench_compare.py --limit 40 --status NOT_FOUND

    # --status NOT_FOUND  — сравнивать на «потерянных» (где сейчас не найдено)
    # --status parsed     — на любых с анкетой;  без --status — на всех с raw_spec
    # --search            — доп.: прогнать DuckDuckGo для обоих запросов и сравнить кол-во кандидатов
"""
import os
import sys
import json
import time
import argparse
import asyncio

import asyncpg
from ollama import Client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")   # чтобы найти matching
from matching.prompt_build import build_for_tender                       # новый путь
# старый общий промпт и парсер JSON берём из боевого process_specs (не дублируем)
from process_specs import SYSTEM_PROMPT as OLD_SYSTEM_PROMPT, extract_json

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")


def _chat(client, system, user):
    resp = client.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        stream=False, think=False,
    )
    return extract_json(resp["message"]["content"])


def run_old(client, raw_spec, lot_name):
    user = f"Текст ТЗ:\n{raw_spec[:6000]}\n\nВерни JSON по схеме."
    return _chat(client, OLD_SYSTEM_PROMPT, user)


def run_new(client, raw_spec, lot_name):
    b = build_for_tender(lot_name, raw_spec)
    user = (f"Имя лота: {lot_name}\n" if lot_name else "") + \
           f"Текст ТЗ:\n{raw_spec[:6000]}\n\nВерни JSON по схеме."
    data = _chat(client, b["system"], user)
    if b["category_hint"]:
        data["category"] = b["category_hint"]
    data["_mode"] = b["mode"]
    return data


def ddgs_count(query):
    """Сколько кандидатов даёт DuckDuckGo по паре площадок (для --search)."""
    try:
        from ddgs import DDGS
        n = 0
        for site in ("kaspi.kz", "satu.kz"):
            r = DDGS().text(f"site:{site} {query}", region="ru-ru", max_results=3)
            n += len(r or [])
            time.sleep(0.4)
        return n
    except Exception:
        return -1


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--status", default="", help="NOT_FOUND | parsed | (пусто = все с raw_spec)")
    ap.add_argument("--search", action="store_true", help="сравнить кол-во кандидатов DDGS")
    args = ap.parse_args()

    if not OLLAMA_API_KEY:
        print("❌ Нет OLLAMA_API_KEY"); sys.exit(1)

    client = Client(host="https://ollama.com",
                    headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"})
    conn = await asyncpg.connect(DATABASE_URL)

    where = "raw_spec IS NOT NULL"
    if args.status:
        where += f" AND match_status = '{args.status}'" if args.status != "parsed" \
                 else " AND stage='parsed'"
    rows = await conn.fetch(
        f"SELECT id, name, raw_spec, structured_spec FROM tenders WHERE {where} "
        f"ORDER BY random() LIMIT {args.limit}")
    print(f"Сравнение старый↔новый на {len(rows)} тендерах | модель {OLLAMA_MODEL}"
          + (" | +поиск" if args.search else ""))

    import csv
    out = open("bench_compare.csv", "w", newline="", encoding="utf-8")
    w = csv.writer(out)
    w.writerow(["name", "old_query", "new_query", "new_mode", "new_category",
                "query_changed", "old_cand", "new_cand"])

    changed = got_cat = kz_fixed = better_cand = 0
    for i, r in enumerate(rows, 1):
        name = r["name"]
        try:
            old = await asyncio.to_thread(run_old, client, r["raw_spec"], name)
            new = await asyncio.to_thread(run_new, client, r["raw_spec"], name)
        except Exception as e:
            print(f"[{i}] ⚠️ {name[:40]}: {str(e)[:60]}")
            continue

        oq = (old.get("search_query") or "").strip()
        nq = (new.get("search_query") or "").strip()
        chg = oq.lower() != nq.lower()
        changed += chg
        if new.get("category"):
            got_cat += 1
        # эвристика «починили казахский»: в старом были кирилл. казах. маркеры, в новом — нет
        if any(x in oq.lower() for x in ("ұ", "қ", "ә", "ө", "ғ", "arnal", "үшін")) and \
           not any(x in nq.lower() for x in ("ұ", "қ", "ә", "ө", "ғ")):
            kz_fixed += 1

        oc = nc = ""
        if args.search:
            oc = await asyncio.to_thread(ddgs_count, oq) if oq else 0
            nc = await asyncio.to_thread(ddgs_count, nq) if nq else 0
            if isinstance(oc, int) and isinstance(nc, int) and nc > oc:
                better_cand += 1

        w.writerow([name[:60], oq, nq, new.get("_mode"), new.get("category", ""),
                    "да" if chg else "", oc, nc])
        mark = "✎" if chg else " "
        print(f"[{i}] {mark} {name[:34]:<35} [{new.get('_mode')}]")
        print(f"      старый: {oq[:60]}")
        print(f"      новый : {nq[:60]}  → {new.get('category','')}")

    out.close()
    await conn.close()
    n = len(rows) or 1
    print(f"\n═══ ИТОГ ({len(rows)} лотов) ═══")
    print(f"  запрос изменился:      {changed}/{len(rows)} ({100*changed//n}%)")
    print(f"  получили category:     {got_cat}/{len(rows)}")
    print(f"  починили казахский:    {kz_fixed}")
    if args.search:
        print(f"  новый дал БОЛЬШЕ кандидатов: {better_cand}/{len(rows)}")
    print(f"  детали → bench_compare.csv")


if __name__ == "__main__":
    asyncio.run(main())
