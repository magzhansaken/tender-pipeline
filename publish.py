"""
Публикация на витрину.

Перекладывает из таблицы tenders (рабочая база пайплайна) в таблицу lots (которую
читает сайт) только те тендеры, что:
  - подобраны (match_status = FOUND_EXACT или FOUND_PARTIAL),
  - ещё живые (is_closed = false и срок приёма заявок не прошёл).

Витрина каждый раз пересобирается заново, поэтому старые/просроченные тендеры
автоматически исчезают с сайта (и тестовые демо-лоты тоже).

    python publish.py
"""
import os
import asyncio

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")

INSERT_SQL = """
INSERT INTO lots (
    row_id, name, status, category,
    brand_in_spec, model_in_spec,
    found_brand, found_model, found_product, source_url, source_site,
    matched_specs, missing_specs, conflicts, confidence, reason,
    lot_price, customer, quantity, unit, updated_at
)
SELECT
    t.id,
    t.name,
    t.match_status,
    t.structured_spec->>'product_type',
    t.structured_spec->>'brand',
    t.structured_spec->>'model',
    t.match_result->>'brand',
    t.match_result->>'model',
    t.match_result->>'product_name',
    COALESCE(t.found_url, t.match_result->>'source_url'),
    t.match_result->>'source_site',
    COALESCE(t.match_result->'matched_specs', '[]'::jsonb),
    COALESCE(t.match_result->'missing_specs', '[]'::jsonb),
    COALESCE(t.match_result->'conflicts', '[]'::jsonb),
    t.confidence,
    t.match_result->>'reason',
    t.price_per_unit,
    t.customer,
    t.quantity::int,
    t.unit,
    now()
FROM tenders t
WHERE t.match_status IN ('FOUND_EXACT', 'FOUND_PARTIAL')
  AND t.is_closed = false
  AND (t.deadline IS NULL OR t.deadline >= now())
"""


async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    async with conn.transaction():
        await conn.execute("DELETE FROM lots")
        await conn.execute(INSERT_SQL)
    total = await conn.fetchval("SELECT count(*) FROM lots")
    exact = await conn.fetchval("SELECT count(*) FROM lots WHERE status = 'FOUND_EXACT'")
    partial = await conn.fetchval("SELECT count(*) FROM lots WHERE status = 'FOUND_PARTIAL'")
    await conn.close()
    print(f"Опубликовано на витрину: {total} (точных {exact}, частичных {partial})")


if __name__ == "__main__":
    asyncio.run(main())
