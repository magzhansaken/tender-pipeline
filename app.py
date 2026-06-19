"""
TenderView API — только чтение готовых лотов.

Почему это держит ~1000 одновременных пользователей на одном сервере:
  • данные меняются раз в день (после ночного прогона пайплайна),
  • все ответы помечены Cache-Control → Cloudflare отдаёт их из кэша,
  • эндпоинты асинхронные, к БД — пул соединений asyncpg.
Реальная тяжёлая работа (LLM + поиск) происходит офлайн и сюда не попадает.
"""
import os
import json
from pathlib import Path
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Query, Response, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
STATIC_DIR = Path(__file__).parent / "static"


async def _init_conn(con: asyncpg.Connection) -> None:
    # jsonb-поля приходят/уходят как обычные list/dict
    for t in ("jsonb", "json"):
        await con.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=2, max_size=10, command_timeout=15, init=_init_conn
    )
    yield
    await app.state.pool.close()


app = FastAPI(title="TenderView", lifespan=lifespan)

SORTS = {
    "confidence": "confidence DESC NULLS LAST, updated_at DESC",
    "recent": "updated_at DESC",
    "name": "name ASC",
    "margin": "margin_pct DESC NULLS LAST, confidence DESC",
}

COLS = (
    "row_id, name, status, category, category_type, found_brand, found_model, found_product, "
    "source_url, source_site, confidence, candidates_found, matched_specs, missing_specs, "
    "conflicts, reason, lot_price, purchase_price, margin, margin_pct, "
    "quantity, unit, announce_id, margin_total, updated_at"
)


def _cache(resp: Response, max_age: int = 60, s_maxage: int = 600) -> None:
    # max-age — кэш браузера, s-maxage — кэш Cloudflare.
    # Когда настроишь ежедневный прогон — смело поднимай s-maxage до ~86400.
    resp.headers["Cache-Control"] = (
        f"public, max-age={max_age}, s-maxage={s_maxage}, stale-while-revalidate=120"
    )


@app.get("/api/lots")
async def list_lots(
    response: Response,
    q: str | None = None,
    status: str | None = None,
    category: str | None = None,
    sort: str = "confidence",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    where: list[str] = []
    args: list = []

    if q and q.strip():
        args.append(f"%{q.strip()}%")
        p = len(args)
        where.append(
            f"(name ILIKE ${p} OR found_product ILIKE ${p} "
            f"OR found_brand ILIKE ${p} OR found_model ILIKE ${p})"
        )
    if status:
        args.append(status)
        where.append(f"status = ${len(args)}")
    if category:
        args.append(category)
        where.append(f"category = ${len(args)}")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order_sql = SORTS.get(sort, SORTS["confidence"])

    pool = app.state.pool
    async with pool.acquire() as con:
        total = await con.fetchval(f"SELECT count(*) FROM lots {where_sql}", *args)
        rows = await con.fetch(
            f"SELECT {COLS} FROM lots {where_sql} ORDER BY {order_sql} "
            f"LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
            *args, limit, offset,
        )

    _cache(response)
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/api/lots/{row_id}")
async def get_lot(row_id: int, response: Response):
    pool = app.state.pool
    async with pool.acquire() as con:
        row = await con.fetchrow(
            f"SELECT {COLS}, brand_in_spec, model_in_spec, time_sec "
            f"FROM lots WHERE row_id = $1",
            row_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Лот не найден")
    _cache(response)
    return dict(row)


@app.get("/api/stats")
async def stats(response: Response):
    pool = app.state.pool
    async with pool.acquire() as con:
        by_status = await con.fetch("SELECT status, count(*) AS c FROM lots GROUP BY status")
        total = await con.fetchval("SELECT count(*) FROM lots")
        avg_conf = await con.fetchval("SELECT round(avg(confidence)) FROM lots WHERE confidence > 0")
        cats = await con.fetch(
            "SELECT category, count(*) AS c FROM lots "
            "WHERE category IS NOT NULL GROUP BY category ORDER BY c DESC"
        )
    _cache(response)
    return {
        "total": total or 0,
        "avg_confidence": int(avg_conf) if avg_conf is not None else None,
        "by_status": {r["status"]: r["c"] for r in by_status},
        "categories": [r["category"] for r in cats],
    }


@app.get("/healthz")
async def healthz():
    pool = app.state.pool
    async with pool.acquire() as con:
        await con.fetchval("SELECT 1")
    return {"ok": True}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
