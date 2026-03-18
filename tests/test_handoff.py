import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient
from app.services import handoff
from app.services.handoff import HANDOFF_KEYWORDS, RETURN_TO_AI_KEYWORDS
from app.main import app
from app.config import get_settings


# ---------------------------------------------------------------------------
# detect_handoff_intent — keyword coverage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("keyword", HANDOFF_KEYWORDS)
def test_detect_handoff_intent_all_human_keywords(keyword: str) -> None:
    assert handoff.detect_handoff_intent(keyword) == "human"


@pytest.mark.parametrize("keyword", RETURN_TO_AI_KEYWORDS)
def test_detect_handoff_intent_all_ai_keywords(keyword: str) -> None:
    assert handoff.detect_handoff_intent(keyword) == "ai"


def test_detect_handoff_intent_no_match_returns_none() -> None:
    assert handoff.detect_handoff_intent("Hello how are you") is None
    assert handoff.detect_handoff_intent("") is None


def test_detect_handoff_intent_case_insensitive() -> None:
    assert handoff.detect_handoff_intent("TALK TO HUMAN") == "human"
    assert handoff.detect_handoff_intent("Talk To Human") == "human"


def test_detect_handoff_intent_keyword_with_trailing_punctuation() -> None:
    assert handoff.detect_handoff_intent("talk to human?") == "human"
    assert handoff.detect_handoff_intent("talk to human!") == "human"
    assert handoff.detect_handoff_intent("talk to human please.") == "human"


def test_detect_handoff_intent_keyword_embedded_in_sentence() -> None:
    assert handoff.detect_handoff_intent("I want to talk to human now") == "human"
    assert handoff.detect_handoff_intent("please back to ai thanks") == "ai"


# ---------------------------------------------------------------------------
# detect_handoff_intent — edge cases
# ---------------------------------------------------------------------------

def test_detect_handoff_intent_human_takes_priority_over_ai() -> None:
    """When both a human keyword AND an AI keyword appear in the same message,
    'human' must win because the human-check runs first in detect_handoff_intent."""
    combined = "talk to human and back to ai"
    result = handoff.detect_handoff_intent(combined)
    assert result == "human", (
        "Human intent should take priority over AI intent when both keywords are present"
    )


def test_detect_handoff_intent_thai_keyword_embedded_in_sentence() -> None:
    """Thai keywords embedded mid-sentence.

    detect_handoff_intent uses substring matching (k in text_lower), so any keyword
    found anywhere in the text triggers the intent — exact-word boundaries are NOT
    required.  This test documents that confirmed behaviour.
    """
    thai_sentence = "ฉันต้องการคุยกับเจ้าหน้าที่ด้วย"
    result = handoff.detect_handoff_intent(thai_sentence)
    assert result == "human", (
        "Thai human keyword embedded in a longer sentence should still be detected "
        "because detection uses substring (not exact-word) matching"
    )


# ---------------------------------------------------------------------------
# execute_handoff_to_human
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_handoff_to_human_updates_db_and_sends_farewell() -> None:
    conn = AsyncMock()

    with patch("app.services.zendesk.send_reply", new_callable=AsyncMock) as mock_send:
        await handoff.execute_handoff_to_human(conn, "conv_123", "app_123")

    conn.execute.assert_called_once()
    sql, conv_id = conn.execute.call_args[0]
    assert "agent_mode = 'human'" in sql
    assert conv_id == "conv_123"

    mock_send.assert_called_once()
    reply_text: str = mock_send.call_args[0][2]
    assert "กำลังโอนสาย" in reply_text


@pytest.mark.asyncio
async def test_execute_handoff_to_human_zendesk_failure_is_caught() -> None:
    """Zendesk failure during handoff must be logged, not crash the worker."""
    conn = AsyncMock()

    with patch("app.services.zendesk.send_reply", side_effect=Exception("zendesk down")):
        # Should NOT raise
        await handoff.execute_handoff_to_human(conn, "conv_123", "app_123")

    # DB update still happened
    conn.execute.assert_called_once()


@pytest.mark.asyncio
async def test_execute_handoff_to_human_sql_sets_agent_mode_and_human_requested_at() -> None:
    """SQL must update both agent_mode='human' AND human_requested_at in the same statement."""
    conn = AsyncMock()

    with patch("app.services.zendesk.send_reply", new_callable=AsyncMock):
        await handoff.execute_handoff_to_human(conn, "conv_456", "app_456")

    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "agent_mode = 'human'" in sql
    assert "human_requested_at" in sql


@pytest.mark.asyncio
async def test_execute_handoff_to_human_zendesk_http_status_error_is_swallowed() -> None:
    """An httpx.HTTPStatusError from zendesk.send_reply must not propagate.

    BUG NOTE: execute_handoff_to_human accepts app_id as a parameter but ignores it,
    always using settings.sunco_app_id instead.  The test passes "app_other" to confirm
    the function still runs without error regardless (tracked in issue #26).
    """
    conn = AsyncMock()

    request = httpx.Request("POST", "http://example.com")
    response = httpx.Response(500, request=request)
    error = httpx.HTTPStatusError("500 error", request=request, response=response)

    with patch("app.services.zendesk.send_reply", side_effect=error):
        await handoff.execute_handoff_to_human(conn, "conv_err", "app_other")

    conn.execute.assert_called_once()


# ---------------------------------------------------------------------------
# execute_return_to_ai
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_return_to_ai_updates_db_and_sends_confirmation() -> None:
    conn = AsyncMock()

    with patch("app.services.zendesk.send_reply", new_callable=AsyncMock) as mock_send:
        await handoff.execute_return_to_ai(conn, "conv_123", "app_123")

    conn.execute.assert_called_once()
    sql, conv_id = conn.execute.call_args[0]
    assert "agent_mode = 'ai'" in sql
    assert conv_id == "conv_123"

    mock_send.assert_called_once()
    reply_text: str = mock_send.call_args[0][2]
    assert "AI" in reply_text


@pytest.mark.asyncio
async def test_execute_return_to_ai_zendesk_failure_is_caught() -> None:
    """Zendesk failure during return-to-AI must be caught, not crash the worker."""
    conn = AsyncMock()

    with patch("app.services.zendesk.send_reply", side_effect=Exception("zendesk down")):
        await handoff.execute_return_to_ai(conn, "conv_123", "app_123")

    conn.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Worker (flush_buffer) — human-keyword path
# ---------------------------------------------------------------------------

def _make_handoff_pool(conn: AsyncMock) -> MagicMock:
    """Build a pool whose acquire() yields conn."""
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_handoff_conn(agent_mode: str = "ai", buffer_messages: list[str] | None = None) -> AsyncMock:
    conn = AsyncMock()
    tx = MagicMock()
    tx.__aenter__ = AsyncMock()
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    conn.fetchrow = AsyncMock(return_value={
        "agent_mode": agent_mode,
        "channel": "line",
        "app_id": "app_test",
        "is_first_msg_sent": False,
    })
    rows = [{"body": m} for m in (buffer_messages or [])]
    conn.fetch = AsyncMock(return_value=rows)
    return conn


@pytest.mark.asyncio
async def test_flush_buffer_human_keyword_triggers_execute_handoff_to_human() -> None:
    """When the buffer contains a human handoff keyword, flush_buffer must call
    execute_handoff_to_human and must NOT call rag.ask."""
    from app.worker import flush_buffer

    conn = _make_handoff_conn(buffer_messages=["talk to human"])
    ctx = {"pool": _make_handoff_pool(conn)}

    with (
        patch("app.worker.get_settings"),
        patch("app.worker.handoff.execute_handoff_to_human", new_callable=AsyncMock) as mock_handoff,
        patch("app.worker.handoff.execute_return_to_ai", new_callable=AsyncMock),
        patch("app.worker.rag.ask", new_callable=AsyncMock) as mock_rag,
    ):
        await flush_buffer(ctx, "conv_hk")

    mock_handoff.assert_called_once_with(conn, "conv_hk", "app_test")
    mock_rag.assert_not_called()


@pytest.mark.asyncio
async def test_flush_buffer_human_keyword_in_multiline_buffer() -> None:
    """Buffer with 2 messages joined; keyword is in the second message — handoff still fires."""
    from app.worker import flush_buffer

    conn = _make_handoff_conn(buffer_messages=["สวัสดี", "talk to human please"])
    ctx = {"pool": _make_handoff_pool(conn)}

    with (
        patch("app.worker.get_settings"),
        patch("app.worker.handoff.execute_handoff_to_human", new_callable=AsyncMock) as mock_handoff,
        patch("app.worker.handoff.execute_return_to_ai", new_callable=AsyncMock),
        patch("app.worker.rag.ask", new_callable=AsyncMock) as mock_rag,
    ):
        await flush_buffer(ctx, "conv_ml")

    mock_handoff.assert_called_once()
    mock_rag.assert_not_called()


@pytest.mark.asyncio
async def test_flush_buffer_handoff_skips_rag_and_state_update() -> None:
    """After triggering a human handoff, flush_buffer returns early:
    - rag.ask must NOT be called
    - insert_outbound_message must NOT be called
    - the is_first_msg_sent UPDATE must NOT be called on conn directly
    """
    from app.worker import flush_buffer

    conn = _make_handoff_conn(buffer_messages=["real person please"])
    ctx = {"pool": _make_handoff_pool(conn)}

    with (
        patch("app.worker.get_settings"),
        patch("app.worker.handoff.execute_handoff_to_human", new_callable=AsyncMock),
        patch("app.worker.handoff.execute_return_to_ai", new_callable=AsyncMock),
        patch("app.worker.rag.ask", new_callable=AsyncMock) as mock_rag,
        patch("app.worker.persistence.insert_outbound_message", new_callable=AsyncMock) as mock_insert,
    ):
        await flush_buffer(ctx, "conv_skip")

    mock_rag.assert_not_called()
    mock_insert.assert_not_called()
    for call in conn.execute.call_args_list:
        sql = call[0][0] if call[0] else ""
        assert "is_first_msg_sent" not in sql, (
            "is_first_msg_sent UPDATE must not be called when handoff fires"
        )


# ---------------------------------------------------------------------------
# Handoff HTTP router
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_handoff_pool():
    """Override get_pool dependency for the handoff router and return the mock pool."""
    from app.db import get_pool

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    app.dependency_overrides[get_pool] = lambda: mock_pool
    yield mock_pool
    app.dependency_overrides.pop(get_pool, None)


@pytest.mark.asyncio
async def test_get_handoff_status_returns_conversation_mode(mock_handoff_pool: MagicMock) -> None:
    """GET /handoff/{id}/status returns 200 with agent_mode and human_requested_at."""
    mock_handoff_pool.fetchrow = AsyncMock(
        return_value={"agent_mode": "human", "human_requested_at": "2026-01-01T00:00:00"}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/handoff/conv_123/status")

    assert response.status_code == 200
    data = response.json()
    assert data["agent_mode"] == "human"
    assert "human_requested_at" in data


@pytest.mark.asyncio
async def test_get_handoff_status_404_for_unknown_conversation(mock_handoff_pool: MagicMock) -> None:
    """GET /handoff/{id}/status returns 404 when the conversation does not exist."""
    mock_handoff_pool.fetchrow = AsyncMock(return_value=None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/handoff/unknown_conv/status")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_post_handoff_human_rejects_missing_api_key(mock_handoff_pool: MagicMock) -> None:
    """POST /handoff/{id}/human without X-API-KEY header returns 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/handoff/conv_123/human")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_handoff_human_rejects_invalid_api_key(mock_handoff_pool: MagicMock) -> None:
    """POST /handoff/{id}/human with wrong X-API-KEY returns 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/handoff/conv_123/human",
            headers={"x-api-key": "definitely-wrong-key"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_handoff_human_success_calls_execute_handoff(mock_handoff_pool: MagicMock) -> None:
    """POST /handoff/{id}/human with valid key + existing conversation calls execute_handoff_to_human."""
    settings = get_settings()
    mock_handoff_pool.fetchrow = AsyncMock(return_value={"app_id": "app_xyz"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.routers.handoff.handoff.execute_handoff_to_human", new_callable=AsyncMock) as mock_exec:
            response = await ac.post(
                "/handoff/conv_123/human",
                headers={"x-api-key": settings.conversations_webhook_secret},
            )

    assert response.status_code == 200
    assert response.json()["mode"] == "human"
    mock_exec.assert_called_once()


@pytest.mark.asyncio
async def test_post_handoff_human_404_for_unknown_conversation(mock_handoff_pool: MagicMock) -> None:
    """POST /handoff/{id}/human returns 404 when conversation does not exist."""
    settings = get_settings()
    mock_handoff_pool.fetchrow = AsyncMock(return_value=None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/handoff/unknown_conv/human",
            headers={"x-api-key": settings.conversations_webhook_secret},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_post_handoff_ai_rejects_invalid_api_key(mock_handoff_pool: MagicMock) -> None:
    """POST /handoff/{id}/ai with wrong X-API-KEY returns 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/handoff/conv_123/ai",
            headers={"x-api-key": "wrong-key-again"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_handoff_ai_success_calls_execute_return_to_ai(mock_handoff_pool: MagicMock) -> None:
    """POST /handoff/{id}/ai with valid key + existing conversation calls execute_return_to_ai."""
    settings = get_settings()
    mock_handoff_pool.fetchrow = AsyncMock(return_value={"app_id": "app_xyz"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("app.routers.handoff.handoff.execute_return_to_ai", new_callable=AsyncMock) as mock_exec:
            response = await ac.post(
                "/handoff/conv_123/ai",
                headers={"x-api-key": settings.conversations_webhook_secret},
            )

    assert response.status_code == 200
    assert response.json()["mode"] == "ai"
    mock_exec.assert_called_once()
