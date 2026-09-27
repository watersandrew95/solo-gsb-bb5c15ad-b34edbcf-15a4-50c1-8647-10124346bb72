"""FastAPI 入口：启动建库、请求级事务中间件、健康检查。"""
from __future__ import annotations

import math
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import config
from .database import init_db, session
from .routers import reports, showcases

APP_TITLE = "博物馆展柜照度预算 API"


def _sanitize_json(value):
    """递归剔除 NaN / Infinity：校验失败回显原始输入时保证响应仍是合法 JSON。"""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _sanitize_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json(v) for v in value]
    return value


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 启动时自动初始化数据库（建表幂等）
        init_db()
        yield

    app = FastAPI(title=APP_TITLE, version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def db_session_middleware(request: Request, call_next):
        # 健康检查不占用写连接
        if request.url.path == "/health":
            return await call_next(request)
        with session() as conn:
            request.state.db = conn
            return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # 默认处理器直接 json.dumps 回显 input；NaN/Infinity 会让响应序列化抛
        # ValueError 退化成 500。这里清洗后统一返回标准结构的 422。
        detail = []
        for err in exc.errors():
            err = dict(err)
            if "input" in err:
                err["input"] = _sanitize_json(err["input"])
            detail.append(err)
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder({"detail": detail}),
        )

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_handler(request: Request, exc: sqlite3.IntegrityError):
        return JSONResponse(status_code=409, content={"detail": f"数据冲突：{exc}"})

    @app.get("/", tags=["meta"])
    def root():
        return {"name": APP_TITLE, "docs": "/docs", "health": "/health"}

    @app.get("/health", tags=["meta"])
    def health():
        # 真实执行一次查询，确认数据库文件可用而不仅是进程存活
        try:
            init_db()
            with session() as conn:
                conn.execute("SELECT 1").fetchone()
        except Exception as exc:  # pragma: no cover - 故障路径
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "database": str(config.DB_PATH), "error": str(exc)},
            )
        return {"status": "healthy", "database": str(config.DB_PATH)}

    app.include_router(showcases.router)
    app.include_router(reports.router)
    return app


app = create_app()
