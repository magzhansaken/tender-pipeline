"""
Воркер обработки ТЗ через Ollama Cloud.

Последовательно берёт собранные тендеры, у которых есть сырой текст ТЗ (raw_spec),
но ещё нет анкеты (structured_spec), прогоняет каждый через модель и сохраняет
структурированную анкету обратно в базу.

Запуск (ключ берётся из переменной окружения, в коде его НЕТ):
    OLLAMA_API_KEY=... python process_specs.py

Полезные переменные окружения:
    OLLAMA_API_KEY  - ключ Ollama Cloud (ОБЯЗАТЕЛЬНО; только в .env, не в коде!)
    OLLAMA_MODEL    - модель (по умолчанию gpt-oss:20b)
    DATABASE_URL    - строка подключения к базе
    LIMIT           - обработать не больше N штук (0 = все; удобно для теста: LIMIT=5)
    DELAY           - пауза между запросами к модели, сек (по умолчанию 0.5)

Возобновляемость: берёт только тендеры со стадией 'collected' и пустой анкетой.
Успех -> стадия 'parsed'. Сбой -> 'parse_error' (чтобы не зациклиться). Повторный
запуск продолжит с оставшихся.
"""
import os
import sys
import json
import asyncio

import asyncpg
from ollama import Client

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
LIMIT = int(os.getenv("LIMIT", "0"))
DELAY = float(os.getenv("DELAY", "0.5"))

SYSTEM_PROMPT = (
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
)


def extract_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if "```" in raw:
        raw = "\n".join(
            ln for ln in raw.split("\n") if not ln.strip().startswith("```")
        ).strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("JSON не найден в ответе модели")
    return json.loads(raw[start:end])


def normalize(client: Client, raw_spec: str) -> dict:
    """Один вызов модели -> разобранная анкета (с проверкой выдуманного бренда)."""
    resp = client.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Текст ТЗ:\n{raw_spec[:6000]}\n\nВерни JSON по схеме."},
        ],
        stream=False,
        think=False,
    )
    data = extract_json(resp["message"]["content"])

    # Анти-галлюцинация: бренд должен реально присутствовать в тексте ТЗ
    brand = data.get("brand")
    if brand and str(brand).lower() not in raw_spec.lower():
        data["_brand_warning"] = f"бренд '{brand}' выдуман моделью — в ТЗ его нет"
        data["brand"] = None
        data["brand_required"] = False

    return data


async def main():
    if not OLLAMA_API_KEY:
        print("❌ Не задан OLLAMA_API_KEY. Положи ключ в .env на сервере и передай в окружении. Воркер не запущен.")
        sys.exit(1)

    client = Client(
        host="https://ollama.com",
        headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
    )

    conn = await asyncpg.connect(DATABASE_URL)

    sel = (
        "SELECT id, lot_number, name, raw_spec FROM tenders "
        "WHERE raw_spec IS NOT NULL AND structured_spec IS NULL AND stage = 'collected' "
        "ORDER BY collected_at"
    )
    if LIMIT > 0:
        sel += f" LIMIT {LIMIT}"

    rows = await conn.fetch(sel)
    total = len(rows)
    print(f"К обработке: {total} тендеров | модель: {OLLAMA_MODEL}")
    if total == 0:
        await conn.close()
        print("Нет необработанных тендеров (stage='collected' с пустой анкетой).")
        return

    done = 0
    err = 0
    for i, r in enumerate(rows, 1):
        data = None
        last_err = None
        # до 3 попыток (на случай лимитов/сетевых сбоев)
        for attempt in range(3):
            try:
                data = await asyncio.to_thread(normalize, client, r["raw_spec"])
                break
            except Exception as e:
                last_err = e
                await asyncio.sleep(1.5 * (attempt + 1))

        if data is None:
            err += 1
            await conn.execute("UPDATE tenders SET stage='parse_error' WHERE id=$1", r["id"])
            print(f"[{i}/{total}] ❌ {r['lot_number']}: {str(last_err)[:120]}")
        else:
            await conn.execute(
                "UPDATE tenders SET structured_spec=$1::jsonb, stage='parsed' WHERE id=$2",
                json.dumps(data, ensure_ascii=False),
                r["id"],
            )
            done += 1
            ptype = str(data.get("product_type", "?"))[:30]
            sq = str(data.get("search_query", ""))[:50]
            nattr = len(data.get("attributes", []) or [])
            print(f"[{i}/{total}] ✅ {r['lot_number']}: {ptype} | параметров: {nattr} | поиск: {sq}")

        await asyncio.sleep(DELAY)

    remaining = await conn.fetchval(
        "SELECT count(*) FROM tenders WHERE structured_spec IS NULL AND stage='collected'"
    )
    await conn.close()
    print(f"\nГотово: обработано {done}, ошибок {err}. Осталось 'collected': {remaining}")


if __name__ == "__main__":
    asyncio.run(main())
