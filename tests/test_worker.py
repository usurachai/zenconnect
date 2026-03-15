import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app import worker


@pytest.fixture
def mock_ctx():
    ctx = {}
    pool = MagicMock()
    ctx["pool"] = pool

    conn = AsyncMock()
    # Mock pool.acquire() context manager
    pool.acquire.return_value.__aenter__.return_value = conn

    # Mock conn.transaction() context manager
    transaction_manager = MagicMock()
    transaction_manager.__aenter__ = AsyncMock()
    transaction_manager.__aexit__ = AsyncMock()
    conn.transaction = MagicMock(return_value=transaction_manager)

    return ctx, conn


@pytest.mark.asyncio
async def test_flush_buffer_human_mode_aborts(mock_ctx):
    ctx, conn = mock_ctx
    # Mock conversation record in human mode
    conn.fetchrow.return_value = {"agent_mode": "human"}

    await worker.flush_buffer(ctx, "conv_123")

    # Check that it selected for update
    conn.fetchrow.assert_called_once()
    assert "SELECT" in conn.fetchrow.call_args[0][0]
    assert "FOR UPDATE" in conn.fetchrow.call_args[0][0]

    # Check that no RAG call would have happened (though we haven't mocked it yet)
    # For now, just ensuring it returns early


@pytest.mark.asyncio
async def test_flush_buffer_happy_path(mock_ctx):
    ctx, conn = mock_ctx
    # Mock conversation record in ai mode
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "app_id": "app_123",
        "is_first_msg_sent": False,
    }
    # Mock buffer messages - now returned from DELETE RETURNING via conn.fetch
    conn.fetch.return_value = [{"body": "Hello"}, {"body": "where are you?"}]

    with (
        patch("app.services.rag.ask", new_callable=AsyncMock) as mock_ask,
        patch(
            "app.services.persistence.get_conversation_history", new_callable=AsyncMock
        ) as mock_history,
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", new_callable=AsyncMock) as mock_send_reply,
    ):
        mock_history.return_value = []
        mock_ask.return_value = "I am here!"

        await worker.flush_buffer(ctx, "conv_123")

        # Verify RAG called with concatenated text
        mock_ask.assert_called_once()
        assert "Hello\nwhere are you?" == mock_ask.call_args[0][0]

        mock_send_reply.assert_called_once()

        # Verify DELETE was called via fetch (DELETE RETURNING)
        assert any("DELETE FROM message_buffer" in str(app[0]) for app in conn.fetch.call_args_list)

        # Verify UPDATE conversations was called
        assert any("UPDATE conversations" in str(app[0]) for app in conn.execute.call_args_list)
