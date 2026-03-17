"""Shared fixtures for all test modules."""
import pytest
from unittest.mock import MagicMock, AsyncMock

import opentelemetry.trace as _trace_module
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource

from app.config import Settings
from app.models import WebhookEvent


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        database_url="postgresql://test",
        redis_url="redis://test",
        conversations_webhook_secret="test-secret",
        sunco_key_id="sunco_key",
        sunco_key_secret="sunco_secret",
        sunco_app_id="app_123",
        integration_key_id="int_key",
        integration_key_secret="int_secret",
        zendesk_subdomain="testdomain",
        zendesk_api_token="zd_token",
        zendesk_agent_group_id="group_1",
        rag_base_url="http://rag-service",
        rag_api_key="rag_key",
        flush_buffer_debounce_seconds=30,
    )


# ---------------------------------------------------------------------------
# Database / Redis mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    return pool


@pytest.fixture
def mock_conn() -> AsyncMock:
    """Async connection with a transaction context manager."""
    conn = AsyncMock()
    tx = MagicMock()
    tx.__aenter__ = AsyncMock()
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


@pytest.fixture
def mock_redis() -> MagicMock:
    redis = MagicMock()
    redis.enqueue_job = AsyncMock()
    return redis


# ---------------------------------------------------------------------------
# Worker context
# ---------------------------------------------------------------------------

@pytest.fixture
def worker_ctx(mock_conn: AsyncMock) -> tuple[dict, AsyncMock]:
    """Returns (ctx dict, conn) with pool.acquire() wired up."""
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = mock_conn
    ctx = {"pool": pool, "redis": MagicMock()}
    return ctx, mock_conn


# ---------------------------------------------------------------------------
# Webhook event factory
# ---------------------------------------------------------------------------

def make_webhook_event(
    *,
    event_id: str = "evt_123",
    conv_id: str = "conv_123",
    msg_id: str = "msg_123",
    channel: str = "line",
    author_type: str = "user",
    content_type: str = "text",
    text: str = "Hello",
) -> WebhookEvent:
    return WebhookEvent.model_validate(
        {
            "id": event_id,
            "createdAt": "2026-03-14T02:20:32.440Z",
            "type": "conversation:message",
            "payload": {
                "conversation": {"id": conv_id, "type": "personal", "brandId": "brand_123"},
                "message": {
                    "id": msg_id,
                    "received": "2026-03-14T02:20:32.440Z",
                    "author": {"userId": "user_123", "displayName": "Test User", "type": author_type},
                    "content": {"type": content_type, "text": text},
                    "source": {
                        "type": channel,
                        "integrationId": "int_123",
                        "client": {
                            "integrationId": "int_123",
                            "type": channel,
                            "externalId": "ext_123",
                            "id": "client_123",
                        },
                    },
                },
            },
        }
    )


@pytest.fixture
def sample_event() -> WebhookEvent:
    return make_webhook_event()


# ---------------------------------------------------------------------------
# OpenTelemetry reset (needed for any test touching tracing globals)
# ---------------------------------------------------------------------------

@pytest.fixture
def reset_tracer_provider():
    """Reset OTEL global state before and after each test."""
    _trace_module._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    _trace_module._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    trace.set_tracer_provider(provider)
    yield
    _trace_module._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    _trace_module._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
