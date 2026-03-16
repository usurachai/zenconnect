import json
import asyncpg
import structlog
from datetime import datetime
from typing import Any
from app.models import WebhookEvent
from arq import ArqRedis

logger = structlog.get_logger()


async def insert_webhook_event(
    pool: asyncpg.Pool, event: WebhookEvent, raw_payload: dict[str, Any]
) -> None:
    query = """
        INSERT INTO webhook_events (event_id, conversation_id, raw_payload, received_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (event_id) DO NOTHING
    """
    conv_id = event.payload.conversation.id if event.payload.conversation else None
    await pool.execute(
        query,
        event.id,
        conv_id,
        json.dumps(raw_payload),
        datetime.fromisoformat(event.createdAt.replace("Z", "+00:00")),
    )


async def upsert_conversation(pool: asyncpg.Pool, event: WebhookEvent) -> None:
    payload = event.payload
    conv = payload.conversation
    msg = payload.message

    if not conv or not msg:
        return

    query = """
        INSERT INTO conversations (
            conversation_id, app_id, channel, user_id, display_name, avatar_url, last_replied_at, last_message_received_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
        ON CONFLICT (conversation_id) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            avatar_url = EXCLUDED.avatar_url,
            app_id = EXCLUDED.app_id,
            last_message_received_at = NOW()
    """
    client = msg.source.client if msg.source else None

    await pool.execute(
        query,
        conv.id,
        msg.source.integrationId,  # Using integrationId as proxy for app_id / unique channel id
        msg.source.type,
        msg.author.userId,
        msg.author.displayName or "Unknown",
        client.avatarUrl if client else None,
    )


async def insert_message(pool: asyncpg.Pool, event: WebhookEvent) -> None:
    payload = event.payload
    msg = payload.message
    conv = payload.conversation

    if not msg or not conv:
        return

    query = """
        INSERT INTO messages (message_id, conversation_id, author_type, channel, body, received_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (message_id) DO NOTHING
    """
    await pool.execute(
        query,
        msg.id,
        conv.id,
        msg.author.type,
        msg.source.type,
        msg.content.text or "",
        datetime.fromisoformat(msg.received.replace("Z", "+00:00")),
    )


async def insert_message_buffer(pool: asyncpg.Pool, event: WebhookEvent) -> None:
    payload = event.payload
    msg = payload.message
    conv = payload.conversation

    if not msg or not conv:
        return

    query = """
        INSERT INTO message_buffer (conversation_id, message_id, body)
        VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING
    """
    await pool.execute(query, conv.id, msg.id, msg.content.text or "")


async def enqueue_flush(redis: ArqRedis, conversation_id: str) -> None:
    # Enqueue immediately - worker will handle debounce timing
    # ARQ's job ID deduplication handles duplicate enqueues
    await redis.enqueue_job(
        "flush_buffer",
        conversation_id,
        _job_id=f"flush_buffer:{conversation_id}",
    )


async def get_conversation_history(
    conn: asyncpg.Connection | asyncpg.Pool, conversation_id: str, limit: int = 10
) -> list[dict[str, str]]:
    """
    Fetches the last N messages for the conversation, formatted for meowRAG.
    """
    query = """
        SELECT author_type, body 
        FROM messages 
        WHERE conversation_id = $1 
        ORDER BY received_at DESC 
        LIMIT $2
    """
    rows = await conn.fetch(query, conversation_id, limit)

    # Reverse to get chronological order [oldest -> newest]
    history = []
    for row in reversed(rows):
        role = "user" if row["author_type"] == "user" else "assistant"
        history.append({"role": role, "content": row["body"]})

    return history


async def insert_outbound_message(
    conn: asyncpg.Connection | asyncpg.Pool, conversation_id: str, body: str
) -> None:
    """
    Inserts a message sent by the AI agent into the messages table.
    """
    query = """
        INSERT INTO messages (message_id, conversation_id, author_type, channel, body, received_at)
        VALUES ($1, $2, $3, $4, $5, NOW())
    """
    # Generate a unique ID for the outbound message since it's not coming from a webhook event
    import uuid

    message_id = f"outbound_{uuid.uuid4()}"

    # We need to look up the channel from the last conversation state
    conv = await conn.fetchrow(
        "SELECT channel FROM conversations WHERE conversation_id = $1", conversation_id
    )
    channel = conv["channel"] if conv else "api"

    await conn.execute(query, message_id, conversation_id, "business", channel, body)
