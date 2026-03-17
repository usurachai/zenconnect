import pytest
from unittest.mock import MagicMock, AsyncMock
from app.services import persistence
from app.models import WebhookEvent
from app.config import Settings


@pytest.fixture
def mock_settings():
    return Settings(
        database_url="postgresql://test",
        redis_url="redis://test",
        conversations_webhook_secret="test",
        sunco_key_id="test",
        sunco_key_secret="test",
        sunco_app_id="test",
        integration_key_id="test",
        integration_key_secret="test",
        zendesk_subdomain="test",
        zendesk_api_token="test",
        zendesk_agent_group_id="test",
        rag_base_url="http://test",
        rag_api_key="test",
        flush_buffer_debounce_seconds=30,
    )


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock()
    return pool


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.enqueue_job = AsyncMock()
    return redis


@pytest.fixture
def sample_event():
    return WebhookEvent.model_validate(
        {
            "id": "evt_123",
            "createdAt": "2026-03-14T02:20:32.440Z",
            "type": "conversation:message",
            "payload": {
                "conversation": {"id": "conv_123", "type": "personal", "brandId": "brand_123"},
                "message": {
                    "id": "msg_123",
                    "received": "2026-03-14T02:20:32.440Z",
                    "author": {"userId": "user_123", "displayName": "Test User", "type": "user"},
                    "content": {"type": "text", "text": "Hello persistence"},
                    "source": {
                        "type": "line",
                        "integrationId": "int_123",
                        "client": {
                            "integrationId": "int_123",
                            "type": "line",
                            "externalId": "ext_123",
                            "id": "client_123",
                        },
                    },
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_insert_webhook_event(mock_pool, sample_event):
    raw_payload = {"test": "data"}
    await persistence.insert_webhook_event(mock_pool, sample_event, raw_payload)
    mock_pool.execute.assert_called_once()
    args = mock_pool.execute.call_args[0]
    assert "INSERT INTO webhook_events" in args[0]
    assert args[1] == "evt_123"
    assert args[2] == "conv_123"
    assert args[3] == __import__("json").dumps(raw_payload)


@pytest.mark.asyncio
async def test_upsert_conversation(mock_pool, sample_event):
    await persistence.upsert_conversation(mock_pool, sample_event)
    mock_pool.execute.assert_called_once()
    args = mock_pool.execute.call_args[0]
    assert "INSERT INTO conversations" in args[0]
    assert "ON CONFLICT (conversation_id) DO UPDATE" in args[0]
    assert args[1] == "conv_123"


@pytest.mark.asyncio
async def test_insert_message(mock_pool, sample_event):
    await persistence.insert_message(mock_pool, sample_event)
    mock_pool.execute.assert_called_once()
    args = mock_pool.execute.call_args[0]
    assert "INSERT INTO messages" in args[0]
    assert args[1] == "msg_123"


@pytest.mark.asyncio
async def test_enqueue_flush_defers_by_settings(mock_redis, mock_settings):
    from datetime import timedelta
    from unittest.mock import patch
    with (
        patch("app.services.persistence.get_settings", return_value=mock_settings),
        patch("app.telemetry.get_current_trace_id", return_value=None),
    ):
        await persistence.enqueue_flush(mock_redis, "conv_123")
        mock_redis.enqueue_job.assert_called_once_with(
            "flush_buffer",
            "conv_123",
            _job_id="flush:conv_123",
            _defer_by=timedelta(seconds=mock_settings.flush_buffer_debounce_seconds),
            parent_trace_id=None,
        )


@pytest.mark.asyncio
async def test_enqueue_flush_passes_parent_trace_id(mock_redis, mock_settings):
    from datetime import timedelta
    from unittest.mock import patch
    trace_id = "a" * 32
    with (
        patch("app.services.persistence.get_settings", return_value=mock_settings),
        patch("app.telemetry.get_current_trace_id", return_value=trace_id),
    ):
        await persistence.enqueue_flush(mock_redis, "conv_123")
        mock_redis.enqueue_job.assert_called_once_with(
            "flush_buffer",
            "conv_123",
            _job_id="flush:conv_123",
            _defer_by=timedelta(seconds=mock_settings.flush_buffer_debounce_seconds),
            parent_trace_id=trace_id,
        )


@pytest.mark.asyncio
async def test_insert_message_buffer(mock_pool, sample_event):
    await persistence.insert_message_buffer(mock_pool, sample_event)
    mock_pool.execute.assert_called_once()
    args = mock_pool.execute.call_args[0]
    assert "INSERT INTO message_buffer" in args[0]
    assert args[1] == "conv_123"
    assert args[2] == "msg_123"
    assert args[3] == "Hello persistence"


@pytest.mark.asyncio
async def test_insert_message_buffer_skips_when_no_message(mock_pool, sample_event):
    from app.models import WebhookEvent
    event_no_msg = WebhookEvent.model_validate({
        "id": "evt_x",
        "createdAt": "2026-03-14T02:20:32.440Z",
        "type": "conversation:message",
        "payload": {},
    })
    await persistence.insert_message_buffer(mock_pool, event_no_msg)
    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_conversation_history_returns_chronological_order():
    from unittest.mock import AsyncMock
    conn = AsyncMock()
    # fetch returns newest-first (DESC), function reverses to oldest-first
    conn.fetch.return_value = [
        {"author_type": "business", "body": "Newest reply"},
        {"author_type": "user", "body": "Middle question"},
        {"author_type": "user", "body": "First message"},
    ]

    history = await persistence.get_conversation_history(conn, "conv_123", limit=3)

    assert history[0]["content"] == "First message"
    assert history[1]["content"] == "Middle question"
    assert history[2]["content"] == "Newest reply"


@pytest.mark.asyncio
async def test_get_conversation_history_maps_roles_correctly():
    from unittest.mock import AsyncMock
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"author_type": "business", "body": "AI answer"},
        {"author_type": "user", "body": "User question"},
    ]

    history = await persistence.get_conversation_history(conn, "conv_123")

    roles = {item["content"]: item["role"] for item in history}
    assert roles["User question"] == "user"
    assert roles["AI answer"] == "assistant"


@pytest.mark.asyncio
async def test_get_conversation_history_passes_limit():
    from unittest.mock import AsyncMock
    conn = AsyncMock()
    conn.fetch.return_value = []

    await persistence.get_conversation_history(conn, "conv_123", limit=5)

    args = conn.fetch.call_args[0]
    assert args[1] == "conv_123"
    assert args[2] == 5


@pytest.mark.asyncio
async def test_insert_outbound_message():
    from unittest.mock import AsyncMock
    conn = AsyncMock()
    conn.fetchrow.return_value = {"channel": "line"}

    await persistence.insert_outbound_message(conn, "conv_123", "Hello from AI")

    conn.execute.assert_called_once()
    args = conn.execute.call_args[0]
    assert "INSERT INTO messages" in args[0]
    # message_id is outbound_<uuid>
    assert args[1].startswith("outbound_")
    assert args[2] == "conv_123"
    assert args[3] == "business"
    assert args[4] == "line"
    assert args[5] == "Hello from AI"
