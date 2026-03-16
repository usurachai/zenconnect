import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.config import get_settings

from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture(autouse=True)
def mock_infra():
    with patch("app.routers.webhook.get_pool") as mock_get_pool:
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value={"last_message_received_at": None})
        mock_get_pool.return_value = mock_pool

        # We also need to mock request.app.state.redis
        # This is harder via patch on the function but we can patch the transport or the app state
        app.state.redis = AsyncMock()
        yield mock_pool, app.state.redis


@pytest.fixture
def settings():
    return get_settings()


@pytest.mark.asyncio
async def test_webhook_unauthorized():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/webhook/conversations", headers={"x-api-key": "invalid"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_authorized_empty_payload(settings):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook/conversations",
            headers={"x-api-key": settings.conversations_webhook_secret},
            json={
                "app": {"id": "app_123"},
                "webhook": {"id": "wh_123", "version": "v2"},
                "events": [],
            },
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_webhook_filters_non_message_event(settings):
    payload = {
        "app": {"id": "app_123"},
        "webhook": {"id": "wh_123", "version": "v2"},
        "events": [
            {
                "id": "event_1",
                "type": "conversation:read",
                "createdAt": "2026-03-14T02:20:32.440Z",
                "payload": {},
            }
        ],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook/conversations",
            headers={"x-api-key": settings.conversations_webhook_secret},
            json=payload,
        )
    assert response.status_code == 200
    # Implementation should skip processing but return 200


@pytest.fixture
def base_webhook_payload():
    return {
        "app": {"id": "69b431da2a58376b846eb50e"},
        "webhook": {"id": "69b4b35b1f8f194414dbb9d2", "version": "v2"},
        "events": [
            {
                "id": "event_id_123",
                "createdAt": "2026-03-14T02:20:32.440Z",
                "type": "conversation:message",
                "payload": {
                    "conversation": {
                        "id": "conv_id_123",
                        "type": "personal",
                        "brandId": "brand_123",
                    },
                    "message": {
                        "id": "msg_id_123",
                        "received": "2026-03-14T02:20:32.440Z",
                        "author": {
                            "userId": "user_id_123",
                            "displayName": "Test User",
                            "type": "user",
                        },
                        "content": {"type": "text", "text": "Hello AI"},
                        "source": {
                            "type": "line",
                            "integrationId": "int_123",
                            "client": {
                                "integrationId": "int_123",
                                "type": "line",
                                "externalId": "ext_user_123",
                                "id": "client_id_123",
                                "displayName": "Test User",
                            },
                        },
                    },
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_webhook_valid_line_message(settings, base_webhook_payload):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook/conversations",
            headers={"x-api-key": settings.conversations_webhook_secret},
            json=base_webhook_payload,
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_webhook_filters_unsupported_channel(settings, base_webhook_payload):
    base_webhook_payload["events"][0]["payload"]["message"]["source"]["type"] = "web"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook/conversations",
            headers={"x-api-key": settings.conversations_webhook_secret},
            json=base_webhook_payload,
        )
    assert response.status_code == 200
