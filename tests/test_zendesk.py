import pytest
import httpx
import base64
from pytest_httpx import HTTPXMock
from app.services import zendesk
from app.config import Settings


@pytest.fixture
def settings(mock_settings: Settings) -> Settings:
    return mock_settings


@pytest.mark.asyncio
async def test_send_reply_happy_path(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=201, json={})

    # Should not raise
    await zendesk.send_reply("conv_abc", settings.sunco_app_id, "Hello!", settings)


@pytest.mark.asyncio
async def test_send_reply_uses_correct_url(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=201, json={})

    await zendesk.send_reply("conv_abc", "app_xyz", "Hello!", settings)

    request = httpx_mock.get_requests()[0]
    expected_url = (
        f"https://{settings.zendesk_subdomain}.zendesk.com"
        f"/sc/v2/apps/app_xyz/conversations/conv_abc/messages"
    )
    assert str(request.url) == expected_url


@pytest.mark.asyncio
async def test_send_reply_uses_basic_auth(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=201, json={})

    await zendesk.send_reply("conv_abc", settings.sunco_app_id, "Hello!", settings)

    request = httpx_mock.get_requests()[0]
    auth_header = request.headers.get("authorization", "")
    assert auth_header.startswith("Basic ")

    decoded = base64.b64decode(auth_header[6:]).decode()
    assert decoded == f"{settings.integration_key_id}:{settings.integration_key_secret}"


@pytest.mark.asyncio
async def test_send_reply_sends_correct_payload(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=201, json={})

    await zendesk.send_reply("conv_abc", settings.sunco_app_id, "Test message", settings)

    import json
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.read())
    assert body["author"]["type"] == "business"
    assert body["content"]["type"] == "text"
    assert body["content"]["text"] == "Test message"


@pytest.mark.asyncio
async def test_send_reply_raises_on_http_4xx(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=401, text="Unauthorized")

    with pytest.raises(httpx.HTTPStatusError):
        await zendesk.send_reply("conv_abc", settings.sunco_app_id, "Hello!", settings)


@pytest.mark.asyncio
async def test_send_reply_raises_on_http_5xx(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=500, text="Internal Server Error")

    with pytest.raises(httpx.HTTPStatusError):
        await zendesk.send_reply("conv_abc", settings.sunco_app_id, "Hello!", settings)


@pytest.mark.asyncio
async def test_send_reply_raises_on_network_error(
    httpx_mock: HTTPXMock, settings: Settings
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))

    with pytest.raises(Exception):
        await zendesk.send_reply("conv_abc", settings.sunco_app_id, "Hello!", settings)
