"""
Публикация на витрину + РАСЧЁТ МАРЖИ (этап 6).

Перекладывает из таблицы tenders (рабочая база пайплайна) в таблицу lots
(которую читает сайт) только те тендеры, что:
  - подобраны (match_status = FOUND_EXACT или FOUND_PARTIAL),
  - ещё живые (is_closed = false и срок приёма заявок не прошёл).

ДОПОЛНИТЕЛЬНО считает маржу по каждому лоту:
  - берёт цену закупки (price), которую сверка вытащила с карточки товара,
  - переводит её в тенге по площадке (kaspi/satu = тенге, ozon/wb/yandex = рубли,
    alibaba/1688 = юани),
  - маржа за единицу = цена лота - цена закупки,
  - маржа % = (цена лота - закупка) / цена лота * 100.
Где цены закупки нет — маржа остаётся пустой (на сайте просто не показывается).

Витрина каждый раз пересобирается заново, поэтому старые/просроченные тендеры
автоматически исчезают с сайта (и тестовые демо-лоты тоже).

    python publish.py

Переменные окружения (курсы можно подправить, по умолчанию приблизительные):
    RATE_RUB - сколько тенге в рубле (по умолчанию 5.0)
    RATE_CNY - сколько тенге в юане  (по умолчанию 66.0)
    RATE_USD - сколько тенге в долларе (по умолчанию 470.0)
"""
import os
import re
import json
import asyncio

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")

# Курсы к тенге: ЖИВОЙ официальный курс Нацбанка РК (с суточным кешем и откатом
# на дефолт, если сайт недоступен — fx_rate никогда не падает).
try:
    import fx_rate
    RATE_USD = fx_rate.get_rate("USD")
    RATE_RUB = fx_rate.get_rate("RUB")
    RATE_CNY = fx_rate.get_rate("CNY")
except Exception:
    RATE_RUB = float(os.getenv("RATE_RUB", "6.0"))
    RATE_CNY = float(os.getenv("RATE_CNY", "66.0"))
    RATE_USD = float(os.getenv("RATE_USD", "478.0"))


def as_dict(val):
    """jsonb из asyncpg приходит строкой — превращаем в dict."""
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except Exception:
        return {}


def parse_price(val):
    """Вытаскивает число-цену из того, что вернула модель (число или строка)."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        v = float(val)
        return v if v > 0 else None
    s = str(val)
    m = re.search(r"\d[\d \u00a0.,]*\d|\d", s)
    if not m:
        return None
    num = m.group(0).replace("\u00a0", "").replace(" ", "").replace(",", "")
    if "." in num:
        intpart, _, frac = num.partition(".")
        if len(frac) == 3 and intpart:          # 355.000 -> европейский разделитель тысяч
            num = intpart + frac
        else:
            try:
                v = float(num)
                return v if v > 0 else None
            except Exception:
                num = intpart
    try:
        v = float(num)
        return v if v > 0 else None
    except Exception:
        return None


def to_kzt(price, site):
    """Переводит цену закупки в тенге в зависимости от площадки."""
    if price is None:
        return None
    s = (site or "").lower()
    if "1688" in s or "alibaba" in s:
        return price * RATE_CNY
    if s.endswith(".ru") or "ozon" in s or "wildberries" in s or "yandex" in s:
        return price * RATE_RUB
    # .kz, kaspi, satu или неизвестно -> считаем тенге
    return price * 1.0


def to_int(val):
    try:
        return int(round(float(val)))
    except Exception:
        return None


SELECT_SQL = """
SELECT id, lot_number, name, match_status, structured_spec, match_result,
       found_url, confidence, price_per_unit, customer, quantity, unit
FROM tenders
WHERE match_status IN ('FOUND_EXACT', 'FOUND_PARTIAL')
  AND is_closed = false
"""

INSERT_SQL = """
INSERT INTO lots (
    row_id, name, status, category,
    brand_in_spec, model_in_spec,
    found_brand, found_model, found_product, source_url, source_site,
    matched_specs, missing_specs, conflicts, confidence, reason,
    lot_price, purchase_price, margin, margin_pct,
    customer, quantity, unit, announce_id, margin_total, updated_at
) VALUES (
    $1, $2, $3, $4,
    $5, $6,
    $7, $8, $9, $10, $11,
    $12::jsonb, $13::jsonb, $14::jsonb, $15, $16,
    $17, $18, $19, $20,
    $21, $22, $23, $24, $25, now()
)
"""


async def main():
    conn = await asyncpg.connect(DATABASE_URL)

    # новые колонки витрины (создаём, если их ещё нет)
    await conn.execute(
        "ALTER TABLE lots ADD COLUMN IF NOT EXISTS announce_id text;"
        "ALTER TABLE lots ADD COLUMN IF NOT EXISTS margin_total numeric;"
    )

    rows = await conn.fetch(SELECT_SQL)

    records = []
    with_margin = 0
    for r in rows:
        spec = as_dict(r["structured_spec"])
        mr = as_dict(r["match_result"])
        site = mr.get("source_site")

        lot_price = r["price_per_unit"]
        try:
            lot_price = float(lot_price) if lot_price is not None else None
        except Exception:
            lot_price = None

        # Конвертация цены закупки в тенге по ЖИВОМУ курсу (Нацбанк РК).
        # Alibaba — это оптовый ОРИЕНТИР (USD), а НЕ цена закупки: маржу по нему
        # НЕ считаем (иначе вышла бы недостоверная маржа), а показываем отдельной
        # честной строкой в пояснении.
        cur_code = (mr.get("price_currency") or "").upper()
        raw_price = parse_price(mr.get("price"))
        is_alibaba = mr.get("price_source") == "alibaba"
        if is_alibaba:
            purchase = None
        elif cur_code == "KZT":
            purchase = raw_price
        elif cur_code == "USD":
            purchase = raw_price * RATE_USD if raw_price else None
        elif cur_code == "RUB":
            purchase = raw_price * RATE_RUB if raw_price else None
        elif cur_code == "CNY":
            purchase = raw_price * RATE_CNY if raw_price else None
        else:
            purchase = to_kzt(raw_price, site)

        # Пояснение к лоту: для Alibaba добавляем оптовый ориентир в тенге.
        reason_text = mr.get("reason") or ""
        if is_alibaba and raw_price:
            ali_kzt = "{:,}".format(int(round(raw_price * RATE_USD))).replace(",", " ")
            moq = mr.get("ali_moq") or ""
            lo = mr.get("ali_usd_low")
            hi = mr.get("ali_usd_high")
            rng = (" ($%s\u2013$%s)" % (lo, hi)) if (lo and hi) else ""
            note = ("Alibaba опт-ориентир: ~%s \u20b8%s%s \u2014 это ОПТОВАЯ цена с Alibaba "
                    "(USD по курсу Нацбанка), НЕ реальная цена закупки для тендера."
                    % (ali_kzt, (" от " + str(moq)) if moq else "", rng))
            reason_text = (note + (" | " + reason_text if reason_text else "")).strip()

        qty = to_int(r["quantity"])

        ln = r["lot_number"] or ""
        announce_id = ln.split("-")[0] if ln else None

        margin = None
        margin_pct = None
        if lot_price and lot_price > 0 and purchase and purchase > 0:
            margin = round(lot_price - purchase, 2)
            margin_pct = round((lot_price - purchase) / lot_price * 100.0, 1)
            with_margin += 1

        margin_total = round(margin * qty, 2) if (margin is not None and qty) else None

        records.append((
            r["id"],
            r["name"],
            r["match_status"],
            spec.get("product_type"),
            spec.get("brand"),
            spec.get("model"),
            mr.get("brand"),
            mr.get("model"),
            mr.get("product_name"),
            r["found_url"] or mr.get("source_url"),
            site,
            json.dumps(mr.get("matched_specs") or [], ensure_ascii=False),
            json.dumps(mr.get("missing_specs") or [], ensure_ascii=False),
            json.dumps(mr.get("conflicts") or [], ensure_ascii=False),
            r["confidence"],
            reason_text,
            lot_price,
            purchase,
            margin,
            margin_pct,
            r["customer"],
            qty,
            r["unit"],
            announce_id,
            margin_total,
        ))

    async with conn.transaction():
        await conn.execute("DELETE FROM lots")
        if records:
            await conn.executemany(INSERT_SQL, records)

    total = await conn.fetchval("SELECT count(*) FROM lots")
    exact = await conn.fetchval("SELECT count(*) FROM lots WHERE status = 'FOUND_EXACT'")
    partial = await conn.fetchval("SELECT count(*) FROM lots WHERE status = 'FOUND_PARTIAL'")
    await conn.close()
    print(f"Опубликовано на витрину: {total} (точных {exact}, частичных {partial}); с маржой: {with_margin}")


if __name__ == "__main__":
    asyncio.run(main())
