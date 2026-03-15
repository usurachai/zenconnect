from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
import structlog
from arq import create_pool
from arq.connections import RedisSettings
from app.db import init_pool, close_pool
from app.config import get_settings
from app.routers import webhook, handoff, debug

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    await init_pool()
    app.state.redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.redis.close()
    await close_pool()


# Behind Nginx proxy that strips /api/zendesk/
# We use root_path to tell FastAPI the entry point is /api/zendesk
app = FastAPI(
    title="Zendesk AI Agent Service",
    lifespan=lifespan,
    root_path="/api/zendesk",
    docs_url=None,  # Disable default
    redoc_url=None,
    openapi_url=None,  # Disable default
)


@app.get("/health", include_in_schema=False)
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url="./openapi.json",  # Relative path is most robust behind stripping proxies
        title=app.title + " - Swagger UI",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )


@app.get("/openapi.json", include_in_schema=False)
async def get_openapi_endpoint() -> dict[str, Any]:
    from fastapi.openapi.utils import get_openapi

    return get_openapi(
        title=app.title, version=app.version, routes=app.routes, servers=[{"url": "/api/zendesk"}]
    )


app.include_router(webhook.router)
app.include_router(handoff.router)
app.include_router(debug.router)
