import pytest
from unittest.mock import MagicMock, AsyncMock
from app.services import persistence
from app.models import WebhookEvent

@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.execute = AsyncMock()
    return pool

@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.enqueue_job = AsyncMock()
    return redis

@pytest.fixture
def sample_event():
    return WebhookEvent.model_validate({
        "id": "evt_123",
        "createdAt": "2026-03-14T02:20:32.440Z",
        "type": "conversation:message",
        "payload": {
            "conversation": {
                "id": "conv_123",
                "type": "personal",
                "brandId": "brand_123"
            },
            "message": {
                "id": "msg_123",
                "received": "2026-03-14T02:20:32.440Z",
                "author": {
                    "userId": "user_123",
                    "displayName": "Test User",
                    "type": "user"
                },
                "content": {
                    "type": "text",
                    "text": "Hello persistence"
                },
                "source": {
                    "type": "line",
                    "integrationId": "int_123",
                    "client": {
                        "integrationId": "int_123",
                        "type": "line",
                        "externalId": "ext_123",
                        "id": "client_123"
                    }
                }
            }
        }
    })

@pytest.mark.asyncio
async def test_insert_webhook_event(mock_pool, sample_event):
    raw_payload = {"test": "data"}
    await persistence.insert_webhook_event(mock_pool, sample_event, raw_payload)
    mock_pool.execute.assert_called_once()
    args = mock_pool.execute.call_args[0]
    assert "INSERT INTO webhook_events" in args[0]
    assert args[1] == "evt_123"
    assert args[2] == "conv_123"
    assert args[3] == __import__('json').dumps(raw_payload)

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
async def test_enqueue_flush(mock_redis):
    await persistence.enqueue_flush(mock_redis, "conv_123")
    mock_redis.enqueue_job.assert_called_once_with(
        'flush_buffer',
        "conv_123",
        _job_id="flush_buffer:conv_123",
        _defer_by=30
    )
