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
import time
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Query, Response, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
STATIC_DIR = Path(__file__).parent / "static"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")  # пароль админ-панели (из .env)
LOGS_DIR = os.getenv("HOST_LOGS_DIR", "/hostlogs")  # сюда монтируем /opt/tenderview (ro)
WORKER_LOGS = [
    {"name": "Сбор с goszakup",      "file": "cron.log",         "schedule": "раз в сутки (≈21:00)", "max_min": 1560},
    {"name": "Загрузка в базу",       "file": "loader_cron.log",  "schedule": "каждые 5 мин",         "max_min": 20},
    {"name": "Обработка (Оллама)",    "file": "ollama_cron.log",  "schedule": "каждые 10 мин",        "max_min": 35},
    {"name": "Поиск на площадках",    "file": "search_cron.log",  "schedule": "каждые 15 мин",        "max_min": 50},
    {"name": "Публикация на витрину", "file": "publish_cron.log", "schedule": "каждые 15 мин",        "max_min": 50},
    {"name": "Wildberries (цены)",    "file": "wb_cron.log",      "schedule": "каждый час",           "max_min": 150},
    {"name": "Alibaba (ориентиры)",   "file": "alibaba_loop.log", "schedule": "каждые 30 мин",        "max_min": 120},
]


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


# ─────────── Админ-панель (за паролем ADMIN_PASSWORD из .env) ───────────

def check_admin(x_admin_token: str | None = Header(default=None)):
    """Простая защита: заголовок X-Admin-Token должен совпасть с ADMIN_PASSWORD.
    Если пароль не задан в .env — доступ закрыт полностью (безопасно по умолчанию)."""
    if not ADMIN_PASSWORD or x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Доступ запрещён")


@app.get("/api/admin/health")
async def admin_health(_: None = Depends(check_admin)):
    """Здоровье пайплайна: воронка этапов (по живым тендерам) + свежесть данных."""
    pool = app.state.pool
    async with pool.acquire() as con:
        total = await con.fetchval("SELECT count(*) FROM tenders")
        live = await con.fetchval("SELECT count(*) FROM tenders WHERE is_closed = false")
        normalized = await con.fetchval(
            "SELECT count(*) FROM tenders WHERE is_closed = false AND structured_spec IS NOT NULL"
        )
        searched = await con.fetchval(
            "SELECT count(*) FROM tenders WHERE is_closed = false AND match_status IS NOT NULL"
        )
        found = await con.fetchval(
            "SELECT count(*) FROM tenders WHERE is_closed = false "
            "AND match_status IN ('FOUND_EXACT','FOUND_PARTIAL')"
        )
        priced = await con.fetchval(
            "SELECT count(*) FROM tenders WHERE is_closed = false "
            "AND match_status IN ('FOUND_EXACT','FOUND_PARTIAL') "
            "AND (match_result->>'price') IS NOT NULL"
        )
        published = await con.fetchval("SELECT count(*) FROM lots")
        by_status = await con.fetch(
            "SELECT COALESCE(match_status, '(не обработано)') AS s, count(*) AS c "
            "FROM tenders WHERE is_closed = false GROUP BY 1 ORDER BY c DESC"
        )
        last_collected = await con.fetchval("SELECT max(collected_at) FROM tenders")
        new_24h = await con.fetchval(
            "SELECT count(*) FROM tenders WHERE collected_at > now() - interval '24 hours'"
        )
        last_published = await con.fetchval("SELECT max(updated_at) FROM lots")
        coverage = await con.fetch(
            "SELECT COALESCE(match_result->>'source_site','(не указан)') AS site, "
            "count(*) AS total, "
            "count(*) FILTER (WHERE (match_result->>'price') IS NOT NULL) AS with_price "
            "FROM tenders WHERE is_closed = false "
            "AND match_status IN ('FOUND_EXACT','FOUND_PARTIAL') "
            "GROUP BY 1 ORDER BY total DESC"
        )
    return {
        "funnel": {
            "total": total or 0, "live": live or 0, "normalized": normalized or 0,
            "searched": searched or 0, "found": found or 0,
            "priced": priced or 0, "published": published or 0,
        },
        "by_status": {r["s"]: r["c"] for r in by_status},
        "coverage": [
            {"site": r["site"], "total": r["total"], "with_price": r["with_price"]}
            for r in coverage
        ],
        "freshness": {
            "last_collected": last_collected.isoformat() if last_collected else None,
            "new_24h": new_24h or 0,
            "last_published": last_published.isoformat() if last_published else None,
        },
    }


@app.get("/api/admin/workers")
async def admin_workers(_: None = Depends(check_admin)):
    """Статус cron-воркеров по их лог-файлам: когда последний раз писали и что именно."""
    out = []
    now = time.time()
    for w in WORKER_LOGS:
        p = Path(LOGS_DIR) / w["file"]
        item = {
            "name": w["name"], "schedule": w["schedule"], "file": w["file"],
            "exists": False, "mtime": None, "minutes_ago": None,
            "last_line": "", "status": "unknown",
        }
        try:
            if p.exists():
                item["exists"] = True
                mt = p.stat().st_mtime
                item["mtime"] = datetime.fromtimestamp(mt, tz=timezone.utc).isoformat()
                mins = (now - mt) / 60.0
                item["minutes_ago"] = round(mins)
                if mins <= w["max_min"]:
                    item["status"] = "ok"
                elif mins <= w["max_min"] * 3:
                    item["status"] = "late"
                else:
                    item["status"] = "down"
                try:
                    with open(p, "rb") as f:
                        f.seek(0, 2)
                        size = f.tell()
                        f.seek(max(0, size - 4096))
                        tail = f.read().decode("utf-8", "replace")
                    for ln in reversed(tail.splitlines()):
                        if ln.strip():
                            item["last_line"] = ln.strip()[:200]
                            break
                except Exception:
                    pass
        except Exception:
            pass
        out.append(item)
    return {"workers": out, "logs_dir_ok": Path(LOGS_DIR).exists()}


@app.get("/admin")
async def admin_page():
    return FileResponse(str(STATIC_DIR / "admin.html"))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
