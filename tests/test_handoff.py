import pytest
from unittest.mock import AsyncMock, patch
from app.services import handoff
from app.services.handoff import HANDOFF_KEYWORDS, RETURN_TO_AI_KEYWORDS


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
