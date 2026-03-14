import asyncpg
import structlog
from datetime import datetime
from typing import Any
from app.models import WebhookEvent
from arq import ArqRedis

logger = structlog.get_logger()

async def insert_webhook_event(pool: asyncpg.Pool, event: WebhookEvent, raw_payload: dict[str, Any]) -> None:
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
        raw_payload, 
        datetime.fromisoformat(event.createdAt.replace('Z', '+00:00'))
    )

async def upsert_conversation(pool: asyncpg.Pool, event: WebhookEvent) -> None:
    payload = event.payload
    conv = payload.conversation
    msg = payload.message
    
    if not conv or not msg:
        return

    query = """
        INSERT INTO conversations (
            conversation_id, app_id, channel, user_id, display_name, avatar_url, last_replied_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, NOW())
        ON CONFLICT (conversation_id) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            avatar_url = EXCLUDED.avatar_url,
            app_id = EXCLUDED.app_id
    """
    client = msg.source.client if msg.source else None
    
    await pool.execute(
        query,
        conv.id,
        msg.source.integrationId, # Using integrationId as proxy for app_id / unique channel id
        msg.source.type,
        msg.author.userId,
        msg.author.displayName or "Unknown",
        client.avatarUrl if client else None
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
        datetime.fromisoformat(msg.received.replace('Z', '+00:00'))
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
    """
    await pool.execute(
        query,
        conv.id,
        msg.id,
        msg.content.text or ""
    )

async def enqueue_flush(redis: ArqRedis, conversation_id: str) -> None:
    # arq will deduplicate jobs with the same name if they are in the queue
    # Using 'flush_buffer:conv_id' as the unique job id for debouncing
    await redis.enqueue_job(
        'flush_buffer', 
        conversation_id, 
        _job_id=f"flush_buffer:{conversation_id}",
        _defer_by=30
    )
