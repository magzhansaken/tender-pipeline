"""
Загрузка собранных тендеров (CSV от techspec_dumper) в базу tenders.

    python load_tenders.py techspecs_v2.csv

Логика:
- Таблица tenders создаётся автоматически, если её ещё нет.
- Дедуп по lot_number:
    * новый лот        -> вставляется со стадией 'collected';
    * лот уже был      -> просто обновляется last_seen (и срок/название),
                          raw_spec и результаты обработки НЕ затираются.
- raw_spec (сырой текст ТЗ) сохраняется как есть; structured_spec заполнит Ollama позже.
- deadline парсится в настоящую дату-время (для логики "протух / не протух").
"""
import os
import sys
import csv
import asyncio
import datetime

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")

# Поднимаем размер поля для длинных ТЗ
csv.field_size_limit(10_000_000)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
    id              bigserial PRIMARY KEY,
    lot_number      text UNIQUE,                 -- ключ дедупа
    name            text,
    price_per_unit  numeric,
    quantity        numeric,
    unit            text,
    customer        text,
    deadline        timestamptz,                 -- срок окончания приёма заявок
    raw_spec        text,                        -- сырой текст ТЗ (как собрал dumper)
    structured_spec jsonb,                       -- заполнит Ollama (пока NULL)
    spec_source     text DEFAULT 'pdf',
    stage           text DEFAULT 'collected',    -- collected -> parsed -> searched -> verified -> published
    is_closed       boolean DEFAULT false,       -- закрыт/протух -> скрываем
    last_seen       date,                        -- когда последний раз видели в выдаче
    collected_at    timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tenders_stage     ON tenders (stage);
CREATE INDEX IF NOT EXISTS idx_tenders_deadline  ON tenders (deadline);
CREATE INDEX IF NOT EXISTS idx_tenders_lastseen  ON tenders (last_seen);
CREATE INDEX IF NOT EXISTS idx_tenders_closed    ON tenders (is_closed);
"""


def parse_num(v):
    if v is None:
        return None
    s = str(v).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_deadline(v):
    if not v or not str(v).strip():
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


async def main():
    if len(sys.argv) < 2:
        print("Использование: python load_tenders.py <файл.csv>")
        sys.exit(1)
    path = sys.argv[1]

    rows = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        for r in reader:
            if (r.get("lot_number") or "").strip():
                rows.append(r)

    if not rows:
        print("В файле нет строк с lot_number — нечего загружать.")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(SCHEMA)

    today = datetime.date.today()
    new_count = 0
    upd_count = 0

    sql = """
        INSERT INTO tenders
            (lot_number, name, price_per_unit, quantity, unit, customer,
             deadline, raw_spec, last_seen)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        ON CONFLICT (lot_number) DO UPDATE SET
            last_seen = EXCLUDED.last_seen,
            deadline  = COALESCE(EXCLUDED.deadline, tenders.deadline),
            name      = EXCLUDED.name,
            is_closed = false
    """

    async with conn.transaction():
        for r in rows:
            ln = r["lot_number"].strip()
            existed = await conn.fetchval("SELECT 1 FROM tenders WHERE lot_number = $1", ln)
            await conn.execute(
                sql,
                ln,
                clean(r.get("lot_name")),
                parse_num(r.get("price_per_unit")),
                parse_num(r.get("quantity")),
                clean(r.get("unit")),
                clean(r.get("customer")),
                parse_deadline(r.get("deadline")),
                clean(r.get("tech_spec")),
                today,
            )
            if existed:
                upd_count += 1
            else:
                new_count += 1

    total = await conn.fetchval("SELECT count(*) FROM tenders")
    active = await conn.fetchval("SELECT count(*) FROM tenders WHERE is_closed = false")
    await conn.close()

    print(f"Новых: {new_count} | Обновлено (уже были): {upd_count}")
    print(f"Всего в базе: {total} | активных: {active}")


if __name__ == "__main__":
    asyncio.run(main())
