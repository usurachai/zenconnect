import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app import worker


@pytest.fixture
def mock_ctx():
    ctx = {}
    pool = MagicMock()
    ctx["pool"] = pool

    # Add mock redis
    redis = MagicMock()
    redis.exists = AsyncMock(return_value=True)  # Lock exists for tests
    redis.delete = AsyncMock()
    ctx["redis"] = redis

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
    import datetime

    ctx, conn = mock_ctx
    # Mock conversation record in ai mode - set last_replied_at to long ago to avoid debounce check
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
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
    redis.exists = AsyncMock(return_value=True)  # Lock exists
    redis.delete = AsyncMock()

    ctx = {
        "pool": pool,
        "redis": redis,
    }

    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    transaction_manager = MagicMock()
    transaction_manager.__aenter__ = AsyncMock()
    transaction_manager.__aexit__ = AsyncMock()
    conn.transaction = MagicMock(return_value=transaction_manager)

    # Simulate: buffer has 3 messages (simulating messages arrived during wait)
    conn.fetch.return_value = [
        {"body": "Message 1"},
        {"body": "Message 2"},
        {"body": "Message 3"},
    ]

    conn.fetchrow.return_value = {
        "agent_mode": "ai",
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

        # Run flush (note: this will actually wait 10s, so we mock the sleep)
        with patch("app.worker.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await worker.flush_buffer(ctx, "conv_123")

        # Verify: RAG called with ALL 3 messages combined
        mock_ask.assert_called_once()
        call_args = mock_ask.call_args[0]
        combined_text = call_args[0]

        assert "Message 1" in combined_text
        assert "Message 2" in combined_text
        assert "Message 3" in combined_text

        # Verify: Lock released after processing
        redis.delete.assert_called_once_with("flush_lock:conv_123")

        print(f"✓ All 3 messages batched: {combined_text}")


@pytest.mark.asyncio
async def test_lock_prevents_duplicate_enqueues():
    """
    Test that when a flush is in progress (lock exists),
    subsequent messages don't enqueue duplicate jobs.
    """
    from app.services import persistence

    mock_redis = MagicMock()
    # Simulate lock already exists
    mock_redis.set = AsyncMock(return_value=False)  # NX returns False = lock exists

    await persistence.enqueue_flush(mock_redis, "conv_123")

    # Verify: job NOT enqueued because lock exists
    mock_redis.enqueue_job.assert_not_called()
    # Verify: tried to acquire lock
    mock_redis.set.assert_called_once()

    print("✓ Duplicate enqueue prevented when lock exists")


@pytest.mark.asyncio
async def test_lock_allows_new_batch_after_completion():
    """
    Test that after a flush completes (lock released),
    new messages can trigger a new flush.
    """
    from app.services import persistence

    mock_redis = MagicMock()
    # Simulate lock being available (returns True = lock acquired)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.enqueue_job = AsyncMock()

    # First message acquires lock
    await persistence.enqueue_flush(mock_redis, "conv_123")

    # Verify: lock acquired and job enqueued
    mock_redis.set.assert_called_once()
    mock_redis.enqueue_job.assert_called_once()

    print("✓ New batch can start after previous completes")
