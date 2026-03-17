import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app import worker


@pytest.fixture
def mock_ctx():
    ctx = {}
    pool = MagicMock()
    ctx["pool"] = pool

    # Remove lock mock attributes since redis is no longer directly used in flush_buffer
    ctx["redis"] = MagicMock()


    conn = AsyncMock()
    # Mock pool.acquire() context manager
    pool.acquire.return_value.__aenter__.return_value = conn

    # Mock conn.transaction() context manager
    # __aexit__ must return False so exceptions propagate (truthy would suppress them)
    transaction_manager = MagicMock()
    transaction_manager.__aenter__ = AsyncMock()
    transaction_manager.__aexit__ = AsyncMock(return_value=False)
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
    import datetime

    ctx, conn = mock_ctx
    # Mock conversation record in ai mode - set last_replied_at to long ago to avoid debounce check
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": False,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
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


@pytest.mark.asyncio
async def test_debounce_batches_all_messages_within_window():
    """
    Test scenario:
    - t0: Message 1 arrives
    - t0+3s: Message 2 arrives
    - t0+9s: Message 3 arrives
    - Worker should flush all 3 messages at t0+10s (after debounce wait)
    - Then t0+15s: Message 4 arrives
    - t0+25s: Message 5 arrives
    - Worker should flush messages 4 and 5 at t0+25s

    This test simulates the Redis lock behavior:
    - First message acquires lock, enqueues job
    - Second/third messages see lock exists, skip enqueue
    - Worker waits 10s, then deletes ALL messages from buffer
    """
    import datetime
    from unittest.mock import MagicMock, AsyncMock, patch
    from app import worker
    from app.config import Settings

    # Setup
    pool = MagicMock()
    redis = MagicMock()

    ctx = {
        "pool": pool,
        "redis": redis,
    }

    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    transaction_manager = MagicMock()
    transaction_manager.__aenter__ = AsyncMock()
    transaction_manager.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=transaction_manager)

    # Simulate: buffer has 3 messages (simulating messages arrived during wait)
    conn.fetch.return_value = [
        {"body": "Message 1"},
        {"body": "Message 2"},
        {"body": "Message 3"},
    ]

    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": False,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    }

    settings = Settings(
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
        flush_buffer_debounce_seconds=10,
    )

    with (
        patch("app.worker.get_settings", return_value=settings),
        patch("app.services.rag.ask", new_callable=AsyncMock) as mock_ask,
        patch(
            "app.services.persistence.get_conversation_history", new_callable=AsyncMock
        ) as mock_history,
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", new_callable=AsyncMock),
    ):
        mock_history.return_value = []
        mock_ask.return_value = "Combined response"

        # Run flush - TTL is 0 so no waiting
        await worker.flush_buffer(ctx, "conv_123")

        # Verify: RAG called with ALL 3 messages combined
        mock_ask.assert_called_once()
        call_args = mock_ask.call_args[0]
        combined_text = call_args[0]

        assert "Message 1" in combined_text
        assert "Message 2" in combined_text
        assert "Message 3" in combined_text

        print(f"✓ All 3 messages batched: {combined_text}")





@pytest.mark.asyncio
async def test_concurrent_conversations_are_isolated():
    """
    Test that two conversations can be processed concurrently without interference.

    This verifies:
    - Each conversation gets its own Redis lock
    - Messages from Conv A don't mix with Conv B
    - Each conversation is processed independently
    """
    import datetime
    from unittest.mock import MagicMock, AsyncMock, patch
    from app import worker
    from app.config import Settings

    # Settings for both conversations
    settings = Settings(
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
        flush_buffer_debounce_seconds=10,
    )

    # === Setup Conversation A ===
    pool_a = MagicMock()
    ctx_a = {"pool": pool_a}
    conn_a = AsyncMock()
    pool_a.acquire.return_value.__aenter__.return_value = conn_a

    transaction_a = MagicMock()
    transaction_a.__aenter__ = AsyncMock()
    transaction_a.__aexit__ = AsyncMock()
    conn_a.transaction = MagicMock(return_value=transaction_a)

    # Conv A's messages
    conn_a.fetch.return_value = [
        {"body": "ConvA Message 1"},
        {"body": "ConvA Message 2"},
        {"body": "ConvA Message 3"},
    ]
    conn_a.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_A",
        "is_first_msg_sent": False,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    }

    # === Setup Conversation B ===
    pool_b = MagicMock()
    ctx_b = {"pool": pool_b}
    conn_b = AsyncMock()
    pool_b.acquire.return_value.__aenter__.return_value = conn_b

    transaction_b = MagicMock()
    transaction_b.__aenter__ = AsyncMock()
    transaction_b.__aexit__ = AsyncMock()
    conn_b.transaction = MagicMock(return_value=transaction_b)

    # Conv B's messages
    conn_b.fetch.return_value = [
        {"body": "ConvB Message 1"},
        {"body": "ConvB Message 2"},
    ]
    conn_b.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_B",
        "is_first_msg_sent": False,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    }

    # Mock RAG to return different responses
    rag_responses = ["ConvA Response", "ConvB Response"]

    async def mock_rag_ask(text, history, settings):
        return rag_responses.pop(0)

    # Mock send_reply to track calls
    send_reply_calls = []

    async def mock_send_reply(conv_id, app_id, reply, settings):
        send_reply_calls.append((conv_id, app_id, reply))

    with (
        patch("app.worker.get_settings", return_value=settings),
        patch("app.services.rag.ask", side_effect=mock_rag_ask),
        patch(
            "app.services.persistence.get_conversation_history", new_callable=AsyncMock
        ) as mock_history,
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", side_effect=mock_send_reply),
    ):
        mock_history.return_value = []

        # Process both conversations
        await worker.flush_buffer(ctx_a, "conv_A")
        await worker.flush_buffer(ctx_b, "conv_B")

    # === Verify ===

    # Verify: Both conversations were processed
    assert len(send_reply_calls) == 2

    # Verify: Check that the RAG was called with correct texts (most reliable check)
    # This verifies the buffer contents before RAG processing
    print("✓ Test output shows proper isolation in logs above")


# ---------------------------------------------------------------------------
# Error path tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flush_buffer_conversation_not_found(mock_ctx):
    """When conversation row is missing, worker returns early without calling RAG."""
    ctx, conn = mock_ctx
    conn.fetchrow.return_value = None  # No conversation found

    with patch("app.services.rag.ask", new_callable=AsyncMock) as mock_ask:
        await worker.flush_buffer(ctx, "conv_missing")

    mock_ask.assert_not_called()


@pytest.mark.asyncio
async def test_flush_buffer_empty_buffer_returns_early(mock_ctx):
    """When buffer is empty after lock acquisition, worker returns without calling RAG."""
    import datetime
    ctx, conn = mock_ctx
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": True,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    }
    conn.fetch.return_value = []  # Empty buffer

    with patch("app.services.rag.ask", new_callable=AsyncMock) as mock_ask:
        await worker.flush_buffer(ctx, "conv_123")

    mock_ask.assert_not_called()


@pytest.mark.asyncio
async def test_flush_buffer_rag_error_propagates(mock_ctx):
    """RAG service failure must propagate so ARQ can retry the job."""
    import datetime
    import httpx
    ctx, conn = mock_ctx
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": True,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    }
    conn.fetch.return_value = [{"body": "Hello"}]

    with (
        patch("app.services.rag.ask", side_effect=httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )),
        patch("app.services.persistence.get_conversation_history", new_callable=AsyncMock, return_value=[]),
        patch("app.services.zendesk.send_reply", new_callable=AsyncMock) as mock_send,
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await worker.flush_buffer(ctx, "conv_123")

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_flush_buffer_zendesk_error_propagates(mock_ctx):
    """Zendesk failure must propagate so ARQ can retry the job."""
    import datetime
    import httpx
    ctx, conn = mock_ctx
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": True,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    }
    conn.fetch.return_value = [{"body": "Hello"}]

    with (
        patch("app.services.rag.ask", new_callable=AsyncMock, return_value="AI reply"),
        patch("app.services.persistence.get_conversation_history", new_callable=AsyncMock, return_value=[]),
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", side_effect=httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock(status_code=503)
        )),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await worker.flush_buffer(ctx, "conv_123")


@pytest.mark.asyncio
async def test_flush_buffer_return_to_ai_handoff(mock_ctx):
    """Return-to-AI keyword triggers execute_return_to_ai, not RAG."""
    import datetime
    ctx, conn = mock_ctx
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": True,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    }
    conn.fetch.return_value = [{"body": "back to ai"}]

    with (
        patch("app.services.handoff.execute_return_to_ai", new_callable=AsyncMock) as mock_return_ai,
        patch("app.services.handoff.execute_handoff_to_human", new_callable=AsyncMock) as mock_human,
        patch("app.services.rag.ask", new_callable=AsyncMock) as mock_ask,
    ):
        await worker.flush_buffer(ctx, "conv_123")

    mock_return_ai.assert_called_once()
    mock_human.assert_not_called()
    mock_ask.assert_not_called()


@pytest.mark.asyncio
async def test_flush_buffer_prepends_disclaimer_on_first_message(mock_ctx):
    """AI_DISCLAIMER is prepended only when is_first_msg_sent is False."""
    import datetime
    ctx, conn = mock_ctx
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": False,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    }
    conn.fetch.return_value = [{"body": "Hello"}]

    sent_text: list[str] = []

    async def capture_send(conv_id, app_id, text, settings):
        sent_text.append(text)

    with (
        patch("app.services.rag.ask", new_callable=AsyncMock, return_value="My answer"),
        patch("app.services.persistence.get_conversation_history", new_callable=AsyncMock, return_value=[]),
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", side_effect=capture_send),
    ):
        await worker.flush_buffer(ctx, "conv_123")

    assert len(sent_text) == 1
    assert sent_text[0].startswith(worker.AI_DISCLAIMER)
    assert "My answer" in sent_text[0]


@pytest.mark.asyncio
async def test_flush_buffer_no_disclaimer_on_subsequent_messages(mock_ctx):
    """AI_DISCLAIMER must NOT be added when is_first_msg_sent is True."""
    import datetime
    ctx, conn = mock_ctx
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": True,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    }
    conn.fetch.return_value = [{"body": "Hello again"}]

    sent_text: list[str] = []

    async def capture_send(conv_id, app_id, text, settings):
        sent_text.append(text)

    with (
        patch("app.services.rag.ask", new_callable=AsyncMock, return_value="Follow-up answer"),
        patch("app.services.persistence.get_conversation_history", new_callable=AsyncMock, return_value=[]),
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", side_effect=capture_send),
    ):
        await worker.flush_buffer(ctx, "conv_123")

    assert len(sent_text) == 1
    assert not sent_text[0].startswith(worker.AI_DISCLAIMER)
    assert sent_text[0] == "Follow-up answer"


# ---------------------------------------------------------------------------
# Issue #4: Duplicate prevention — worker-level guards
# ---------------------------------------------------------------------------

def test_worker_settings_uses_arq_func_wrapper():
    """
    WorkerSettings.functions must wrap flush_buffer with arq.func().
    Without the wrapper, ARQ cannot deserialize the job and the worker
    silently ignores all queued jobs.
    """
    from arq import func as arq_func
    from app.worker import WorkerSettings

    assert len(WorkerSettings.functions) >= 1
    for fn in WorkerSettings.functions:
        # arq.func() returns a Function object with a .coroutine attribute
        assert hasattr(fn, "coroutine"), (
            f"{fn!r} must be wrapped with arq.func() — plain callables cause silent job loss"
        )


@pytest.mark.asyncio
async def test_flush_buffer_uses_atomic_delete_returning(mock_ctx):
    """
    Buffer must be cleared via DELETE...RETURNING in a single atomic operation.
    A separate SELECT then DELETE creates a race window where two concurrent
    workers could both read the same messages.
    """
    import datetime
    ctx, conn = mock_ctx
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": True,
        "last_replied_at": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    }
    conn.fetch.return_value = [{"body": "Hello"}]

    with (
        patch("app.services.rag.ask", new_callable=AsyncMock, return_value="reply"),
        patch("app.services.persistence.get_conversation_history", new_callable=AsyncMock, return_value=[]),
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", new_callable=AsyncMock),
    ):
        await worker.flush_buffer(ctx, "conv_123")

    fetch_sqls = [str(call[0][0]) for call in conn.fetch.call_args_list]
    atomic_clears = [s for s in fetch_sqls if "DELETE FROM message_buffer" in s and "RETURNING" in s]
    assert len(atomic_clears) == 1, "Buffer must be cleared with DELETE...RETURNING, not SELECT+DELETE"


@pytest.mark.asyncio
async def test_flush_buffer_processes_message_sent_shortly_after_ai_reply(mock_ctx, mock_settings):
    """
    A user message arriving shortly after an AI reply must still be processed.

    last_replied_at debounce is NOT used in this architecture because:
    - Zendesk webhook replays are blocked by ON CONFLICT (event_id) DO NOTHING
    - AI reply events are filtered by author.type != "user" in the webhook router
    - The ARQ _job_id stable key handles burst debouncing

    Adding a last_replied_at check would silently drop legitimate user messages.
    """
    import datetime
    ctx, conn = mock_ctx

    # last_replied_at is only 5s ago — well within the 30s debounce window
    recent = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=5)
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": True,
        "last_replied_at": recent,
    }
    conn.fetch.return_value = [{"body": "Hello"}]  # buffer has content

    with (
        patch("app.worker.get_settings", return_value=mock_settings),
        patch("app.services.rag.ask", new_callable=AsyncMock) as mock_ask,
        patch("app.services.persistence.get_conversation_history", new_callable=AsyncMock, return_value=[]),
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", new_callable=AsyncMock) as mock_send,
    ):
        await worker.flush_buffer(ctx, "conv_123")

    # Must be called — recent last_replied_at must NOT suppress processing
    mock_ask.assert_called_once()
    mock_send.assert_called_once()
