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
import hashlib
import secrets
import hmac
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Query, Response, HTTPException, Depends, Header, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tender:tender@db:5432/tender")
STATIC_DIR = Path(__file__).parent / "static"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")  # пароль админ-панели (из .env)
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "").strip().lower()  # email владельца для автозавода аккаунта
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))  # срок жизни сессии входа, дней

# ─────────── Тарифы и продающая модель (Фаза 2.3) — единый источник правды ───────────
# Стратегия: продаём не «доступ к тендерам», а ГОТОВУЮ ПРИБЫЛЬ — тендер сразу с ценой
# поставщика и рассчитанной маржой (чего нет ни у одного конкурента в мире).
# Годовая цена = ×10 от месячной (2 месяца в подарок). promo_* — «цена основателя»
# для первых N клиентов. Валюта — тенге.
PLANS = {
    "free": {
        "name": "Демо", "monthly": 0, "annual": 0,
        "tagline": "Осмотреться и оценить",
        "features": [
            "Витрина тендеров с goszakup.gov.kz",
            "14 дней полного доступа при регистрации",
            "Дальше — цена и маржа скрыты",
        ],
        "highlight": False, "cta": "Попробовать 14 дней",
    },
    "start": {
        "name": "Старт", "monthly": 14900, "annual": 149000,
        "promo_monthly": 8900, "promo_annual": 89000,
        "tagline": "Частным поставщикам и ИП",
        "features": [
            "Все тендеры с goszakup.gov.kz",
            "Цена поставщика по каждому лоту",
            "Маржа рассчитана автоматически",
            "Лоты отсортированы по выгоде",
            "Поиск, фильтры и уведомления",
        ],
        "highlight": True, "cta": "Попробовать бесплатно",
    },
    "business": {
        "name": "Бизнес", "monthly": 39900, "annual": 399000,
        "promo_monthly": 24900, "promo_annual": 249000,
        "tagline": "Отделам закупок и компаниям",
        "features": [
            "Всё из тарифа «Старт»",
            "До 3 пользователей в аккаунте",
            "Выгрузка лотов в Excel",
            "Расширенная история тендеров",
            "Приоритетная поддержка",
        ],
        "highlight": False, "cta": "Попробовать бесплатно",
    },
    "team": {
        "name": "Под ключ", "monthly": None, "annual": None,
        "tagline": "Агентствам и крупным игрокам",
        "features": [
            "Всё из тарифа «Бизнес»",
            "Доступ к API и безлимит пользователей",
            "Помощь в подготовке и подаче заявок",
            "Персональный менеджер",
            "Индивидуальные условия",
        ],
        "highlight": False, "cta": "Связаться с нами",
    },
}
PLAN_ORDER = ["free", "start", "business", "team"]

# Запуск: «цена основателя» — рычаг срочности для первых клиентов (pre-revenue).
PROMO = {
    "active": True,
    "label": "Цена основателя",
    "note": "первым 50 клиентам — навсегда",
    "seats_left": 50,
    "percent": 40,
}

LOGS_DIR = os.getenv("HOST_LOGS_DIR", "/hostlogs")  # сюда монтируем /opt/tenderview (ro)
WORKER_LOGS = [
    {"name": "Сверка с goszakup (новые+статусы)", "file": "daily_sync_loop.log", "schedule": "каждые 4 часа", "max_min": 290},
    {"name": "Обработка (Оллама)",    "file": "ollama_loop.log",  "schedule": "каждые 10 мин",        "max_min": 35},
    {"name": "Поиск на площадках",    "file": "search_loop.log",  "schedule": "каждые 15 мин",        "max_min": 50},
    {"name": "Публикация на витрину", "file": "publish.log",      "schedule": "каждые 15 мин",        "max_min": 50},
    {"name": "Wildberries (цены)",    "file": "wb_loop.log",      "schedule": "каждый час",           "max_min": 150},
    {"name": "Alibaba (ориентиры)",   "file": "alibaba_loop.log", "schedule": "каждые 30 мин",        "max_min": 120},
]


# ─────────── Безопасность: хеширование паролей и токены сессий (stdlib) ───────────
def hash_password(pw: str) -> str:
    """scrypt-хеш пароля (memory-hard, стойкий). Формат: scrypt$<salt>$<hash>."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32, maxmem=67108864)
    return "scrypt$%s$%s" % (salt.hex(), dk.hex())


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32, maxmem=67108864)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _open_session(con, user_id: int) -> str:
    """Создаёт сессию: возвращает токен клиенту, в базе хранит только его хеш."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    await con.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ($1,$2,$3)",
        _token_hash(token), user_id, expires
    )
    return token


async def _init_auth(pool) -> None:
    """Создаёт таблицы аккаунтов (идемпотентно) и заводит владельца из .env."""
    async with pool.acquire() as con:
        await con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            BIGSERIAL PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'client',
                status        TEXT NOT NULL DEFAULT 'active',
                created_at    TIMESTAMPTZ DEFAULT now(),
                last_login    TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ DEFAULT now(),
                expires_at TIMESTAMPTZ NOT NULL
            );
        """)
        # подписки (Фаза 2.3) — храним прямо в users + журнал платежей
        await con.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS plan            TEXT DEFAULT 'free';
            ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_started_at TIMESTAMPTZ;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_expires_at TIMESTAMPTZ;
            CREATE TABLE IF NOT EXISTS payments (
                id         BIGSERIAL PRIMARY KEY,
                user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                plan       TEXT NOT NULL,
                period     TEXT NOT NULL,                 -- 'month' | 'year'
                amount     INTEGER NOT NULL DEFAULT 0,    -- в тенге
                method     TEXT NOT NULL DEFAULT 'manual',-- manual | kaspi | card | ...
                status     TEXT NOT NULL DEFAULT 'paid',  -- paid | pending | failed
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)
        await con.execute("DELETE FROM sessions WHERE expires_at < now()")  # чистим протухшие
        if OWNER_EMAIL and ADMIN_PASSWORD:
            exists = await con.fetchval("SELECT 1 FROM users WHERE email=$1", OWNER_EMAIL)
            if not exists:
                await con.execute(
                    "INSERT INTO users (email, password_hash, role) VALUES ($1,$2,'owner')",
                    OWNER_EMAIL, hash_password(ADMIN_PASSWORD)
                )


async def _init_conn(con: asyncpg.Connection) -> None:
    # jsonb-поля приходят/уходят как обычные list/dict
    for t in ("jsonb", "json"):
        await con.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=2, max_size=10, command_timeout=15, init=_init_conn
    )
    await _init_auth(app.state.pool)
    yield
    await app.state.pool.close()


app = FastAPI(title="TenderView", lifespan=lifespan)


# ─────────── Аккаунты и авторизация (Фаза 2.1) ───────────
class Credentials(BaseModel):
    email: str
    password: str


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


async def _user_by_token(token: str | None):
    """Ищет активного пользователя по токену сессии. None — нет/протух/заблокирован."""
    if not token:
        return None
    async with app.state.pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT u.id, u.email, u.role, u.status FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = $1 AND s.expires_at > now()", _token_hash(token)
        )
    if not row or row["status"] != "active":
        return None
    return dict(row)


async def current_user(x_auth_token: str | None = Header(default=None)):
    """Возвращает пользователя по токену сессии, либо None (не вошёл)."""
    return await _user_by_token(x_auth_token)


async def require_user(user=Depends(current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return user


async def require_owner(user=Depends(current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Только для владельца")
    return user


def _valid_email(e: str) -> bool:
    e = (e or "").strip()
    return "@" in e and "." in e.split("@")[-1] and 3 <= len(e) <= 200


@app.post("/api/auth/register")
async def auth_register(c: Credentials):
    email = (c.email or "").strip().lower()
    if not _valid_email(email):
        raise HTTPException(status_code=400, detail="Некорректный email")
    if len(c.password or "") < 8:
        raise HTTPException(status_code=400, detail="Пароль минимум 8 символов")
    async with app.state.pool.acquire() as con:
        if await con.fetchval("SELECT 1 FROM users WHERE email=$1", email):
            raise HTTPException(status_code=409, detail="Пользователь с таким email уже есть")
        uid = await con.fetchval(
            "INSERT INTO users (email, password_hash, role) VALUES ($1,$2,'client') RETURNING id",
            email, hash_password(c.password)
        )
        token = await _open_session(con, uid)
    return {"token": token, "email": email, "role": "client"}


@app.post("/api/auth/login")
async def auth_login(c: Credentials):
    email = (c.email or "").strip().lower()
    async with app.state.pool.acquire() as con:
        u = await con.fetchrow("SELECT id, password_hash, role, status FROM users WHERE email=$1", email)
        if not u or not verify_password(c.password or "", u["password_hash"]):
            raise HTTPException(status_code=401, detail="Неверный email или пароль")
        if u["status"] != "active":
            raise HTTPException(status_code=403, detail="Аккаунт заблокирован")
        token = await _open_session(con, u["id"])
        await con.execute("UPDATE users SET last_login=now() WHERE id=$1", u["id"])
    return {"token": token, "email": email, "role": u["role"]}


@app.post("/api/auth/logout")
async def auth_logout(x_auth_token: str | None = Header(default=None)):
    if x_auth_token:
        async with app.state.pool.acquire() as con:
            await con.execute("DELETE FROM sessions WHERE token_hash=$1", _token_hash(x_auth_token))
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(user=Depends(require_user)):
    return {"email": user["email"], "role": user["role"]}


@app.post("/api/auth/change-password")
async def auth_change_password(p: PasswordChange, user=Depends(require_user)):
    if len(p.new_password or "") < 8:
        raise HTTPException(status_code=400, detail="Новый пароль минимум 8 символов")
    async with app.state.pool.acquire() as con:
        u = await con.fetchrow("SELECT password_hash FROM users WHERE id=$1", user["id"])
        if not verify_password(p.old_password or "", u["password_hash"]):
            raise HTTPException(status_code=401, detail="Старый пароль неверен")
        await con.execute("UPDATE users SET password_hash=$1 WHERE id=$2",
                          hash_password(p.new_password), user["id"])
        await con.execute("DELETE FROM sessions WHERE user_id=$1", user["id"])  # пере-логин везде
    return {"ok": True, "note": "Пароль изменён, войдите заново"}

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

async def check_admin(x_admin_token: str | None = Header(default=None)):
    """Доступ к панели: принимает ЛИБО сессию владельца (токен из /login),
    ЛИБО устаревший пароль ADMIN_PASSWORD (аварийный доступ / curl).
    И токен, и пароль приходят в одном заголовке X-Admin-Token."""
    if x_admin_token:
        if ADMIN_PASSWORD and x_admin_token == ADMIN_PASSWORD:
            return
        user = await _user_by_token(x_admin_token)
        if user and user["role"] == "owner":
            return
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

                    def _is_noise(s: str) -> bool:
                        if s.startswith("nohup:"):
                            return True
                        # ID docker-контейнера (длинная hex-строка) — не показываем
                        if len(s) >= 12 and all(c in "0123456789abcdef" for c in s):
                            return True
                        return False

                    for ln in reversed(tail.splitlines()):
                        s = ln.strip()
                        if not s or _is_noise(s):
                            continue
                        item["last_line"] = s[:200]
                        break
                except Exception:
                    pass
        except Exception:
            pass
        out.append(item)
    return {"workers": out, "logs_dir_ok": Path(LOGS_DIR).exists()}


def _jload(v):
    """jsonb из asyncpg может прийти строкой или уже объектом — нормализуем."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return v


@app.get("/api/admin/lot-filter-options")
async def admin_lot_filter_options(_: None = Depends(check_admin)):
    """Списки для выпадашек фильтра: какие статусы и площадки есть у живых лотов."""
    pool = app.state.pool
    async with pool.acquire() as con:
        statuses = await con.fetch(
            "SELECT COALESCE(match_status,'(не обработано)') AS s, count(*) AS c "
            "FROM tenders WHERE is_closed=false GROUP BY 1 ORDER BY c DESC"
        )
        sites = await con.fetch(
            "SELECT COALESCE(match_result->>'source_site','(не указан)') AS s, count(*) AS c "
            "FROM tenders WHERE is_closed=false AND match_status IN ('FOUND_EXACT','FOUND_PARTIAL') "
            "GROUP BY 1 ORDER BY c DESC"
        )
    return {
        "statuses": [{"value": r["s"], "count": r["c"]} for r in statuses],
        "sites": [{"value": r["s"], "count": r["c"]} for r in sites],
    }


@app.get("/api/admin/lots")
async def admin_lots(
    _: None = Depends(check_admin),
    status: str | None = Query(default=None),
    has_price: bool | None = Query(default=None),
    site: str | None = Query(default=None),
    q: str | None = Query(default=None),
    min_sum: float | None = Query(default=None),
    max_sum: float | None = Query(default=None),
    closed: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
):
    """Список лотов из tenders с фильтрами и пагинацией. Сортировка по ценности:
    лоты с ценой и точным совпадением — наверху."""
    conds, args = [], []
    if not closed:
        conds.append("is_closed = false")
    if status:
        args.append(status)
        conds.append(f"COALESCE(match_status,'(не обработано)') = ${len(args)}")
    if has_price is True:
        conds.append("(match_result->>'price') IS NOT NULL")
    elif has_price is False:
        conds.append("(match_result->>'price') IS NULL")
    if site:
        args.append(site)
        conds.append(f"COALESCE(match_result->>'source_site','(не указан)') = ${len(args)}")
    if q:
        args.append(f"%{q}%")
        conds.append(f"(name ILIKE ${len(args)} OR lot_number ILIKE ${len(args)})")
    if min_sum is not None:
        args.append(min_sum)
        conds.append(f"(price_per_unit * quantity) >= ${len(args)}")
    if max_sum is not None:
        args.append(max_sum)
        conds.append(f"(price_per_unit * quantity) <= ${len(args)}")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""

    pool = app.state.pool
    async with pool.acquire() as con:
        total = await con.fetchval(f"SELECT count(*) FROM tenders{where}", *args)
        offset = (page - 1) * per_page
        rows = await con.fetch(
            f"SELECT lot_number, name, match_status, "
            f"COALESCE(match_result->>'source_site','') AS site, "
            f"price_per_unit, quantity, (match_result->>'price') AS found_price, "
            f"is_closed, collected_at "
            f"FROM tenders{where} "
            f"ORDER BY (CASE WHEN (match_result->>'price') IS NOT NULL THEN 0 ELSE 1 END), "
            f"(CASE match_status WHEN 'FOUND_EXACT' THEN 0 WHEN 'FOUND_PARTIAL' THEN 1 "
            f"WHEN 'NOT_FOUND' THEN 2 ELSE 3 END), collected_at DESC NULLS LAST "
            f"LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, per_page, offset,
        )
    out = []
    for r in rows:
        ppu = float(r["price_per_unit"]) if r["price_per_unit"] is not None else None
        qty = r["quantity"]
        out.append({
            "lot_number": r["lot_number"],
            "name": r["name"],
            "status": r["match_status"],
            "site": r["site"],
            "lot_price": ppu,
            "quantity": int(qty) if qty is not None else None,
            "lot_sum": (ppu * float(qty)) if (ppu is not None and qty) else None,
            "found_price": r["found_price"],
            "is_closed": r["is_closed"],
            "collected_at": r["collected_at"].isoformat() if r["collected_at"] else None,
        })
    return {
        "total": total or 0,
        "page": page,
        "per_page": per_page,
        "pages": ((total or 0) + per_page - 1) // per_page,
        "lots": out,
    }


@app.get("/api/admin/lot/{lot_number}")
async def admin_lot_detail(lot_number: str, _: None = Depends(check_admin)):
    """Полная карточка одного лота: ТЗ, анкета Олламы, найденный товар, цена."""
    pool = app.state.pool
    async with pool.acquire() as con:
        r = await con.fetchrow(
            "SELECT lot_number, name, customer, price_per_unit, quantity, unit, "
            "deadline, raw_spec, structured_spec, match_status, match_result, "
            "found_url, stage, is_closed, collected_at, last_seen "
            "FROM tenders WHERE lot_number = $1", lot_number
        )
    if not r:
        raise HTTPException(status_code=404, detail="Лот не найден")
    ppu = float(r["price_per_unit"]) if r["price_per_unit"] is not None else None
    qty = r["quantity"]
    return {
        "lot_number": r["lot_number"],
        "name": r["name"],
        "customer": r["customer"],
        "lot_price": ppu,
        "quantity": int(qty) if qty is not None else None,
        "unit": r["unit"],
        "lot_sum": (ppu * float(qty)) if (ppu is not None and qty) else None,
        "deadline": r["deadline"].isoformat() if r["deadline"] else None,
        "raw_spec": r["raw_spec"],
        "structured_spec": _jload(r["structured_spec"]),
        "match_status": r["match_status"],
        "match_result": _jload(r["match_result"]),
        "found_url": r["found_url"],
        "stage": r["stage"],
        "is_closed": r["is_closed"],
        "collected_at": r["collected_at"].isoformat() if r["collected_at"] else None,
        "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
    }


# ─────────── Управление клиентами (Фаза 2.2) ───────────
@app.get("/api/admin/clients")
async def admin_clients(
    _: None = Depends(check_admin),
    status: str | None = Query(default=None),
    role: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
):
    """Список аккаунтов с фильтрами, пагинацией и сводкой сверху."""
    conds, args = [], []
    if status:
        args.append(status); conds.append(f"status = ${len(args)}")
    if role:
        args.append(role); conds.append(f"role = ${len(args)}")
    if q:
        args.append(f"%{q}%"); conds.append(f"email ILIKE ${len(args)}")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""

    async with app.state.pool.acquire() as con:
        total = await con.fetchval(f"SELECT count(*) FROM users{where}", *args)
        stats = await con.fetchrow("""
            SELECT
              count(*) FILTER (WHERE role='client')                                          AS clients,
              count(*) FILTER (WHERE role='client' AND status='active')                      AS active,
              count(*) FILTER (WHERE status='blocked')                                       AS blocked,
              count(*) FILTER (WHERE role='client' AND created_at > now()-interval '7 days') AS new_7d,
              count(*) FILTER (WHERE role='client' AND plan IS DISTINCT FROM 'free'
                               AND (plan_expires_at IS NULL OR plan_expires_at > now()))     AS paid
            FROM users
        """)
        offset = (page - 1) * per_page
        rows = await con.fetch(
            f"SELECT id, email, role, status, created_at, last_login, plan, plan_expires_at "
            f"FROM users{where} "
            f"ORDER BY created_at DESC NULLS LAST LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, per_page, offset,
        )
    return {
        "total": total or 0,
        "page": page,
        "per_page": per_page,
        "pages": ((total or 0) + per_page - 1) // per_page,
        "stats": {
            "clients": stats["clients"], "active": stats["active"],
            "blocked": stats["blocked"], "new_7d": stats["new_7d"],
            "paid": stats["paid"],
        },
        "clients": [{
            "id": r["id"], "email": r["email"], "role": r["role"], "status": r["status"],
            "plan": r["plan"] or "free",
            "plan_name": PLANS.get(r["plan"] or "free", {}).get("name", r["plan"]),
            "plan_expires_at": r["plan_expires_at"].isoformat() if r["plan_expires_at"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "last_login": r["last_login"].isoformat() if r["last_login"] else None,
        } for r in rows],
    }


@app.post("/api/admin/client/{uid}/block")
async def admin_client_block(uid: int, _: None = Depends(check_admin)):
    async with app.state.pool.acquire() as con:
        u = await con.fetchrow("SELECT role FROM users WHERE id=$1", uid)
        if not u:
            raise HTTPException(status_code=404, detail="Аккаунт не найден")
        if u["role"] == "owner":
            raise HTTPException(status_code=403, detail="Нельзя заблокировать владельца")
        await con.execute("UPDATE users SET status='blocked' WHERE id=$1", uid)
        await con.execute("DELETE FROM sessions WHERE user_id=$1", uid)  # завершаем активные сессии
    return {"ok": True, "status": "blocked"}


@app.post("/api/admin/client/{uid}/unblock")
async def admin_client_unblock(uid: int, _: None = Depends(check_admin)):
    async with app.state.pool.acquire() as con:
        if not await con.fetchval("SELECT 1 FROM users WHERE id=$1", uid):
            raise HTTPException(status_code=404, detail="Аккаунт не найден")
        await con.execute("UPDATE users SET status='active' WHERE id=$1", uid)
    return {"ok": True, "status": "active"}


# ─────────── Подписки и тарифы (Фаза 2.3) ───────────
@app.get("/api/plans")
async def public_plans():
    """Витрина тарифов — публичный эндпоинт для страницы /pricing."""
    return {"plans": [{"key": k, **PLANS[k]} for k in PLAN_ORDER], "promo": PROMO}


class SetPlanBody(BaseModel):
    plan: str
    period: str = "month"      # 'month' | 'year'
    months: int | None = None  # необязательно: точное число месяцев


@app.post("/api/admin/client/{uid}/plan")
async def admin_set_plan(uid: int, body: SetPlanBody, _: None = Depends(check_admin)):
    """Владелец вручную назначает клиенту тариф (оплата приходит позже через эквайринг)."""
    if body.plan not in PLANS:
        raise HTTPException(status_code=400, detail="Неизвестный тариф")
    period = body.period if body.period in ("month", "year") else "month"
    months = body.months if body.months else (12 if period == "year" else 1)

    async with app.state.pool.acquire() as con:
        u = await con.fetchrow("SELECT id, plan, plan_expires_at FROM users WHERE id=$1", uid)
        if not u:
            raise HTTPException(status_code=404, detail="Аккаунт не найден")

        if body.plan == "free":
            await con.execute(
                "UPDATE users SET plan='free', plan_started_at=NULL, plan_expires_at=NULL WHERE id=$1", uid)
            return {"ok": True, "plan": "free", "plan_expires_at": None}

        # продлеваем от большей из дат: сейчас или текущий конец подписки
        base = "GREATEST(now(), COALESCE(plan_expires_at, now()))"
        new_exp = await con.fetchval(
            f"UPDATE users SET plan=$1, "
            f"plan_started_at=COALESCE(plan_started_at, now()), "
            f"plan_expires_at={base} + ($2::int * INTERVAL '1 month') "
            f"WHERE id=$3 RETURNING plan_expires_at",
            body.plan, months, uid)

        amount = (PLANS[body.plan].get("annual") if period == "year" else PLANS[body.plan].get("monthly")) or 0
        await con.execute(
            "INSERT INTO payments (user_id, plan, period, amount, method, status) "
            "VALUES ($1,$2,$3,$4,'manual','paid')",
            uid, body.plan, period, amount)
    return {"ok": True, "plan": body.plan,
            "plan_expires_at": new_exp.isoformat() if new_exp else None}


@app.get("/pricing")
async def pricing_page():
    return FileResponse(str(STATIC_DIR / "pricing.html"))


@app.get("/login")
async def login_page():
    return FileResponse(str(STATIC_DIR / "login.html"))


@app.get("/admin")
async def admin_page():
    return FileResponse(str(STATIC_DIR / "admin.html"))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
