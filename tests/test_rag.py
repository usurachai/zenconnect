import pytest
import httpx
from pytest_httpx import HTTPXMock
from app.services import rag
from tests.conftest import Settings


@pytest.fixture
def settings(mock_settings: Settings) -> Settings:
    return mock_settings


@pytest.mark.asyncio
async def test_ask_returns_answer(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{settings.rag_base_url}/api/v1/ask",
        json={"answer": "Here is your answer"},
        status_code=200,
    )

    result = await rag.ask("What is X?", [], settings)

    assert result == "Here is your answer"


@pytest.mark.asyncio
async def test_ask_sends_correct_payload(httpx_mock: HTTPXMock, settings: Settings) -> None:
    history = [{"role": "user", "content": "previous question"}]
    httpx_mock.add_response(json={"answer": "ok"})

    await rag.ask("my query", history, settings)

    request = httpx_mock.get_requests()[0]
    body = request.read()
    import json
    payload = json.loads(body)
    assert payload["query"] == "my query"
    assert payload["conversation_history"] == history
    assert payload["top_k"] == 5


@pytest.mark.asyncio
async def test_ask_sends_bearer_auth_header(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(json={"answer": "ok"})

    await rag.ask("query", [], settings)

    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"] == f"Bearer {settings.rag_api_key}"


@pytest.mark.asyncio
async def test_ask_returns_fallback_on_missing_answer_key(
    httpx_mock: HTTPXMock, settings: Settings
) -> None:
    httpx_mock.add_response(json={"other_key": "value"})

    result = await rag.ask("query", [], settings)

    assert "ขออภัย" in result  # fallback Thai message


@pytest.mark.asyncio
async def test_ask_returns_fallback_on_empty_answer(
    httpx_mock: HTTPXMock, settings: Settings
) -> None:
    httpx_mock.add_response(json={"answer": ""})

    result = await rag.ask("query", [], settings)

    assert "ขออภัย" in result  # empty answer treated as missing


@pytest.mark.asyncio
async def test_ask_raises_on_http_4xx(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=400, text="Bad Request")

    with pytest.raises(httpx.HTTPStatusError):
        await rag.ask("query", [], settings)


@pytest.mark.asyncio
async def test_ask_raises_on_http_5xx(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(status_code=503, text="Service Unavailable")

    with pytest.raises(httpx.HTTPStatusError):
        await rag.ask("query", [], settings)


@pytest.mark.asyncio
async def test_ask_raises_on_network_error(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))

    with pytest.raises(Exception):
        await rag.ask("query", [], settings)
