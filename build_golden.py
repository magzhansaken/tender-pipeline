"""
Собирает ЗОЛОТОЙ НАБОР — фиксированный список тендеров для тестов промптов/моделей.

Запускается ОДИН раз. Замораживает выбор в golden_set.json и больше его не трогает,
чтобы все эксперименты гонялись на ОДНИХ И ТЕХ ЖЕ тендерах (иначе сравнение нечестное).

Чтобы пересобрать набор заново — удали golden_set.json вручную.

    python build_golden.py
"""
import os
import json
import asyncio

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
OUT = os.getenv("GOLDEN_FILE", "golden_set.json")
N_RANDOM = int(os.getenv("N_RANDOM", "30"))

# Якорные лоты — известные показательные случаи (из наших прогонов):
# найдено точно / частично / не найдено / сложные / казахский запрос.
ANCHORS = [
    "42292333-ОЛ-ЗЦП1",  # РВД — нашли точно (FOUND_EXACT)
    "86883793-ЗЦП5",     # задвижка — частично (FOUND_PARTIAL)
    "83062300-ЗЦП1",     # интерактивная панель — частично
    "42292894-ОЛ-ЗЦП1",  # маркер — казахский запрос, не нашли
    "42292383-ОЛ-ЗЦП1",  # подшипник — сложный
    "87151855-ЗЦП1",     # FPV drone kit — сложный
]


async def main():
    if os.path.exists(OUT):
        print(f"⚠️  {OUT} уже существует — золотой набор ЗАМОРОЖЕН, не трогаю.")
        print("   Чтобы пересобрать заново — удали файл вручную и запусти снова.")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    chosen = {}

    # 1) якоря — что из них есть в базе
    for ln in ANCHORS:
        row = await conn.fetchrow(
            "SELECT lot_number, name, raw_spec FROM tenders "
            "WHERE lot_number = $1 AND raw_spec IS NOT NULL AND length(raw_spec) > 30",
            ln,
        )
        if row:
            chosen[row["lot_number"]] = dict(row)

    # 2) случайные для разнообразия (исключая уже выбранные якоря)
    rows = await conn.fetch(
        "SELECT lot_number, name, raw_spec FROM tenders "
        "WHERE raw_spec IS NOT NULL AND length(raw_spec) > 30 "
        "AND lot_number <> ALL($1::text[]) "
        "ORDER BY random() LIMIT $2",
        list(chosen.keys()) or [""],
        N_RANDOM,
    )
    for r in rows:
        chosen[r["lot_number"]] = dict(r)

    await conn.close()

    data = [
        {"lot_number": v["lot_number"], "name": v["name"], "raw_spec": v["raw_spec"]}
        for v in chosen.values()
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    anchors_found = sum(1 for ln in ANCHORS if ln in chosen)
    print(f"✅ Золотой набор заморожен: {len(data)} тендеров → {OUT}")
    print(f"   Якорей из списка найдено: {anchors_found} из {len(ANCHORS)}")
    print("   Этот файл теперь НЕ меняется — все тесты идут на нём.")


if __name__ == "__main__":
    asyncio.run(main())
