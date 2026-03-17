"""
Issue #10 — Multi-conversation isolation test suite.

Verifies that concurrent flush_buffer executions for different conversations
do not interfere with each other.

Five isolation properties are tested:
  1. Buffer DELETE is scoped to conversation_id (no cross-clearing)
  2. RAG receives only the target conversation's messages
  3. Zendesk reply is routed to the correct conversation
  4. ARQ job keys are per-conversation (debounce is isolated)
  5. Concurrent execution (asyncio.gather) maintains all of the above
"""

import asyncio
import datetime
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app import worker
from app.services import persistence


PAST = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(messages: list[str]) -> AsyncMock:
    """Build a mock asyncpg connection pre-loaded with given buffer messages."""
    conn = AsyncMock()
    tx = MagicMock()
    tx.__aenter__ = AsyncMock()
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    conn.fetchrow.return_value = {
        "agent_mode": "ai",
        "channel": "line",
        "app_id": "app_123",
        "is_first_msg_sent": True,
        "last_replied_at": PAST,
    }
    conn.fetch.return_value = [{"body": m} for m in messages]
    return conn


def _make_pool(*conns: AsyncMock) -> MagicMock:
    """
    Shared pool whose acquire().__aenter__() yields connections in order.
    Simulates asyncpg pool handing out separate connections per acquire().
    """
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.side_effect = list(conns)
    return pool


# ---------------------------------------------------------------------------
# Scenario 1: Buffer DELETE is scoped to conversation_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_buffer_delete_scoped_to_conversation_id(mock_settings):
    """
    DELETE FROM message_buffer must use WHERE conversation_id = $1 with the
    correct id for each flush. Without scoping, worker A would clear worker B's
    buffer, causing messages to be silently lost.
    """
    conn_a = _make_conn(["msg A1", "msg A2"])
    conn_b = _make_conn(["msg B1"])
    ctx = {"pool": _make_pool(conn_a, conn_b)}

    with (
        patch("app.worker.get_settings", return_value=mock_settings),
        patch("app.services.rag.ask", new_callable=AsyncMock, return_value="ok"),
        patch("app.services.persistence.get_conversation_history", new_callable=AsyncMock, return_value=[]),
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", new_callable=AsyncMock),
    ):
        await worker.flush_buffer(ctx, "conv_A")
        await worker.flush_buffer(ctx, "conv_B")

    def _get_delete_conv_id(conn: AsyncMock) -> str:
        for c in conn.fetch.call_args_list:
            if "DELETE FROM message_buffer" in str(c[0][0]):
                return c[0][1]
        raise AssertionError("No DELETE FROM message_buffer call found")

    assert _get_delete_conv_id(conn_a) == "conv_A"
    assert _get_delete_conv_id(conn_b) == "conv_B"


# ---------------------------------------------------------------------------
# Scenario 2: RAG receives only the target conversation's messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rag_receives_only_target_conversation_messages(mock_settings):
    """
    RAG must be called with each conversation's own buffered text only.
    Cross-contamination here would cause the AI to answer using another
    user's context.
    """
    conn_a = _make_conn(["ConvA: question about savings account"])
    conn_b = _make_conn(["ConvB: question about home loan"])
    ctx = {"pool": _make_pool(conn_a, conn_b)}

    rag_inputs: list[str] = []

    async def capture_rag(text: str, history, settings) -> str:
        rag_inputs.append(text)
        return "answer"

    with (
        patch("app.worker.get_settings", return_value=mock_settings),
        patch("app.services.rag.ask", side_effect=capture_rag),
        patch("app.services.persistence.get_conversation_history", new_callable=AsyncMock, return_value=[]),
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", new_callable=AsyncMock),
    ):
        await worker.flush_buffer(ctx, "conv_A")
        await worker.flush_buffer(ctx, "conv_B")

    assert len(rag_inputs) == 2
    assert "savings" in rag_inputs[0] and "loan" not in rag_inputs[0]
    assert "loan" in rag_inputs[1] and "savings" not in rag_inputs[1]


# ---------------------------------------------------------------------------
# Scenario 3: Zendesk reply routed to the correct conversation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reply_routed_to_correct_conversation(mock_settings):
    """
    send_reply must be called with the conversation_id that triggered the flush.
    A wrong routing would send Conv A's reply to Conv B's chat window.
    """
    conn_a = _make_conn(["tell me about my balance"])
    conn_b = _make_conn(["how do I apply for a credit card"])
    ctx = {"pool": _make_pool(conn_a, conn_b)}

    async def mirror_rag(text: str, history, settings) -> str:
        return f"answer_for:{text}"

    send_calls: list[tuple[str, str]] = []

    async def capture_send(conv_id: str, app_id: str, reply: str, settings) -> None:
        send_calls.append((conv_id, reply))

    with (
        patch("app.worker.get_settings", return_value=mock_settings),
        patch("app.services.rag.ask", side_effect=mirror_rag),
        patch("app.services.persistence.get_conversation_history", new_callable=AsyncMock, return_value=[]),
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", side_effect=capture_send),
    ):
        await worker.flush_buffer(ctx, "conv_A")
        await worker.flush_buffer(ctx, "conv_B")

    assert len(send_calls) == 2
    replies = {conv_id: reply for conv_id, reply in send_calls}

    # Content isolation
    assert "balance" in replies["conv_A"]
    assert "credit card" not in replies["conv_A"]
    assert "credit card" in replies["conv_B"]
    assert "balance" not in replies["conv_B"]

    # Routing: each send must carry its own conversation_id
    assert "conv_A" in replies
    assert "conv_B" in replies


# ---------------------------------------------------------------------------
# Scenario 4: ARQ job keys are per-conversation (debounce is isolated)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_flush_job_keys_are_per_conversation(mock_settings):
    """
    Each conversation must get its own ARQ job key (flush:<conv_id>).
    If two conversations shared a job key, the debounce timer for one would
    reset the other's, causing unexpected delays or dropped flushes.
    """
    redis = MagicMock()
    redis.enqueue_job = AsyncMock()

    with (
        patch("app.services.persistence.get_settings", return_value=mock_settings),
        patch("app.telemetry.get_current_trace_id", return_value=None),
    ):
        await persistence.enqueue_flush(redis, "conv_A")
        await persistence.enqueue_flush(redis, "conv_B")
        await persistence.enqueue_flush(redis, "conv_A")  # re-enqueue A (debounce reset)

    job_ids = [c[1]["_job_id"] for c in redis.enqueue_job.call_args_list]

    assert job_ids[0] == "flush:conv_A"
    assert job_ids[1] == "flush:conv_B"
    assert job_ids[2] == "flush:conv_A"  # re-enqueue reuses same key (intentional)

    # Keys are distinct — Conv B's debounce is independent of Conv A's
    assert job_ids[0] != job_ids[1]


# ---------------------------------------------------------------------------
# Scenario 5: True concurrent execution via asyncio.gather
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_flush_no_cross_contamination(mock_settings):
    """
    Both flush_buffer calls run concurrently via asyncio.gather.

    asyncio.sleep(0) inside the RAG mock forces a context switch at the
    most critical point — after messages are read but before the reply is
    sent — so the event loop actually interleaves the two coroutines.

    This is the closest unit-level simulation of two ARQ workers processing
    different conversations at the same time on the same process.
    """
    conn_a = _make_conn(["ConvA msg1", "ConvA msg2", "ConvA msg3"])
    conn_b = _make_conn(["ConvB msg1", "ConvB msg2", "ConvB msg3"])
    ctx = {"pool": _make_pool(conn_a, conn_b)}

    send_calls: list[tuple[str, str]] = []

    async def interleaving_rag(text: str, history, settings) -> str:
        # Force a context switch here — the other coroutine runs during this gap
        await asyncio.sleep(0)
        return f"REPLY:{text}"

    async def capture_send(conv_id: str, app_id: str, reply: str, settings) -> None:
        send_calls.append((conv_id, reply))

    with (
        patch("app.worker.get_settings", return_value=mock_settings),
        patch("app.services.rag.ask", side_effect=interleaving_rag),
        patch("app.services.persistence.get_conversation_history", new_callable=AsyncMock, return_value=[]),
        patch("app.services.persistence.insert_outbound_message", new_callable=AsyncMock),
        patch("app.services.zendesk.send_reply", side_effect=capture_send),
    ):
        await asyncio.gather(
            worker.flush_buffer(ctx, "conv_A"),
            worker.flush_buffer(ctx, "conv_B"),
        )

    assert len(send_calls) == 2

    replies = {conv_id: reply for conv_id, reply in send_calls}

    # Both conversations must have been replied to
    assert "conv_A" in replies
    assert "conv_B" in replies

    # Content isolation — no message from one conversation appears in the other's reply
    assert "ConvA" in replies["conv_A"]
    assert "ConvB" not in replies["conv_A"]
    assert "ConvB" in replies["conv_B"]
    assert "ConvA" not in replies["conv_B"]
