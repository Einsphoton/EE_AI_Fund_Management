"""FastAPI entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

# 在导入任何读环境变量的模块之前，先加载项目根目录的 .env 文件
# （用于注入 CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET 等本地调试变量）
try:
    from dotenv import load_dotenv

    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    for _env_name in (".env", ".env.local"):
        _env_path = _PROJECT_ROOT / _env_name
        if _env_path.exists():
            load_dotenv(_env_path, override=False)
except Exception:
    # python-dotenv 未安装或加载失败时静默忽略，不影响生产容器用系统环境变量
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from .config import settings
from .database import Base, engine, SessionLocal
from .services import skills_service
from . import scheduler as scheduler_mod
from .api import assets as assets_api
from .api import quotes as quotes_api
from .api import settings_api
from .api import skills_api
from .api import advice_api
from .api import dca_api
from .api import chat_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时打印 CF Access 环境变量加载状态，便于本地/容器调试
    import os as _os
    _cf_id = _os.getenv("CF_ACCESS_CLIENT_ID", "")
    _cf_sec = _os.getenv("CF_ACCESS_CLIENT_SECRET", "")
    _cf_hosts = _os.getenv("CF_ACCESS_HOSTS", "einsphoton.ren")
    if _cf_id and _cf_sec:
        print(
            f"[CF Access] Service Token loaded: id={_cf_id[:8]}...{_cf_id[-8:]} "
            f"secret=***({len(_cf_sec)} chars) hosts={_cf_hosts}"
        )
    else:
        print(
            "[CF Access] Service Token NOT loaded. "
            "Requests to einsphoton.ren may be blocked by Cloudflare."
        )

    Base.metadata.create_all(bind=engine)
    # 轻量级 schema 迁移（补字段等），必须在 create_all 之后
    from .database import run_migrations
    run_migrations()
    db = SessionLocal()
    try:
        skills_service.ensure_default_skills(db)
    finally:
        db.close()
    scheduler_mod.start()
    yield
    scheduler_mod.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(assets_api.router)
app.include_router(quotes_api.router)
app.include_router(settings_api.router)
app.include_router(skills_api.router)
app.include_router(advice_api.router)
app.include_router(dca_api.router)
app.include_router(chat_api.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}


# 静态托管前端构建产物（生产模式）
static_dir = Path(settings.static_dir)
if static_dir.exists() and (static_dir / "index.html").exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(static_dir / "assets")),
        name="static-assets",
    )

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        target = static_dir / full_path
        if target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(static_dir / "index.html"))
else:
    @app.get("/")
    def root():
        return {
            "message": f"{settings.app_name} backend running. Frontend not built.",
            "docs": "/docs",
        }
