"""
Загрузка результатов пайплайна в БД.
    python load_results.py results_15_ddgs.json
Повторный запуск обновляет лоты по row_id (upsert), дублей нет.
"""
import os
import sys
import json
import asyncio

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")

COLS = [
    "row_id", "name", "status", "category", "category_type",
    "brand_in_spec", "model_in_spec",
    "found_brand", "found_model", "found_product", "source_url", "source_site",
    "matched_specs", "missing_specs", "conflicts",
    "confidence", "reason", "candidates_found", "time_sec",
]


def _clean(v):
    if v is None:
        return None
    if isinstance(v, str) and v.strip().lower() in ("null", "none", ""):
        return None
    return v


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _values(r: dict) -> list:
    return [
        _int(r.get("row_id")),
        _clean(r.get("name")),
        _clean(r.get("status")),
        _clean(r.get("category")),
        _clean(r.get("category_type")),
        _clean(r.get("brand_in_spec")),
        _clean(r.get("model_in_spec")),
        _clean(r.get("found_brand")),
        _clean(r.get("found_model")),
        _clean(r.get("found_product")),
        _clean(r.get("source_url")),
        _clean(r.get("source_site")),
        r.get("matched_specs") or [],
        r.get("missing_specs") or [],
        r.get("conflicts") or [],
        _int(r.get("confidence")) or 0,
        _clean(r.get("reason")),
        _int(r.get("candidates_found")),
        _float(r.get("time_sec")),
    ]


async def main():
    if len(sys.argv) < 2:
        print("Использование: python load_results.py <путь_к_results.json>")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as fp:
        data = json.load(fp)

    if isinstance(data, dict):
        if "verification" in data:
            v = dict(data["verification"])
            v.setdefault("row_id", data.get("row_id"))
            v.setdefault("name", data.get("lot_name"))
            data = [v]
        else:
            data = data.get("results") or [data]

    placeholders = ", ".join(f"${n}" for n in range(1, len(COLS) + 1))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS if c != "row_id")
    sql = (
        f"INSERT INTO lots ({', '.join(COLS)}) VALUES ({placeholders}) "
        f"ON CONFLICT (row_id) DO UPDATE SET {updates}, updated_at = now()"
    )

    conn = await asyncpg.connect(DATABASE_URL)
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

    n = 0
    async with conn.transaction():
        for r in data:
            if not isinstance(r, dict) or r.get("row_id") is None:
                continue
            await conn.execute(sql, *_values(r))
            n += 1
    await conn.close()
    print(f"Загружено/обновлено лотов: {n}")


if __name__ == "__main__":
    asyncio.run(main())
