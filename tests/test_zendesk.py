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


# ---------------------------------------------------------------------------
# find_ticket_by_conversation_id
# ---------------------------------------------------------------------------

def _tickets_response(ticket_id: int | None) -> dict:
    tickets = [{"id": ticket_id}] if ticket_id is not None else []
    return {"tickets": tickets}


@pytest.mark.asyncio
async def test_find_ticket_by_conversation_id_found(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=200, json=_tickets_response(9876))

    result = await zendesk.find_ticket_by_conversation_id("conv_abc", settings)

    assert result == "9876"


@pytest.mark.asyncio
async def test_find_ticket_by_conversation_id_not_found(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=200, json=_tickets_response(None))

    result = await zendesk.find_ticket_by_conversation_id("conv_abc", settings)

    assert result is None


@pytest.mark.asyncio
async def test_find_ticket_by_conversation_id_uses_correct_url(httpx_mock: HTTPXMock, settings: Settings) -> None:
    from urllib.parse import unquote
    httpx_mock.add_response(status_code=200, json=_tickets_response(None))

    await zendesk.find_ticket_by_conversation_id("conv_xyz", settings)

    request = httpx_mock.get_requests()[0]
    assert f"https://{settings.zendesk_subdomain}.zendesk.com/api/v2/tickets.json" in str(request.url)
    assert "external_id=conv_xyz" in unquote(str(request.url))


@pytest.mark.asyncio
async def test_find_ticket_by_conversation_id_uses_correct_auth(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=200, json=_tickets_response(None))

    await zendesk.find_ticket_by_conversation_id("conv_abc", settings)

    request = httpx_mock.get_requests()[0]
    auth_header = request.headers.get("authorization", "")
    assert auth_header.startswith("Basic ")

    decoded = base64.b64decode(auth_header[6:]).decode()
    assert decoded == f"{settings.zendesk_email}/token:{settings.zendesk_api_token}"


@pytest.mark.asyncio
async def test_find_ticket_by_conversation_id_raises_on_http_error(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=401, text="Unauthorized")

    with pytest.raises(httpx.HTTPStatusError):
        await zendesk.find_ticket_by_conversation_id("conv_abc", settings)


# ---------------------------------------------------------------------------
# assign_ticket
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_ticket_happy_path(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=200, json={})

    await zendesk.assign_ticket(
        "9876",
        settings,
        group_id="group_1",
        priority="high",
        internal_note="Needs human agent",
        tags=["handoff_requested"],
    )


@pytest.mark.asyncio
async def test_assign_ticket_internal_note_is_private(httpx_mock: HTTPXMock, settings: Settings) -> None:
    import json as json_module
    httpx_mock.add_response(status_code=200, json={})

    await zendesk.assign_ticket("9876", settings, internal_note="agent needed")

    request = httpx_mock.get_requests()[0]
    body = json_module.loads(request.read())
    assert body["ticket"]["comment"]["body"] == "agent needed"
    assert body["ticket"]["comment"]["public"] is False


@pytest.mark.asyncio
async def test_assign_ticket_uses_correct_url(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=200, json={})

    await zendesk.assign_ticket("9876", settings)

    request = httpx_mock.get_requests()[0]
    expected_url = f"https://{settings.zendesk_subdomain}.zendesk.com/api/v2/tickets/9876"
    assert str(request.url) == expected_url


@pytest.mark.asyncio
async def test_assign_ticket_raises_on_http_error(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=422, text="Unprocessable Entity")

    with pytest.raises(httpx.HTTPStatusError):
        await zendesk.assign_ticket("9876", settings)
