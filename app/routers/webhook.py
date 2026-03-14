from fastapi import APIRouter, Header, HTTPException, BackgroundTasks, Depends, status, Request
import structlog
import asyncpg
from typing import Any
from arq import ArqRedis
from app.config import Settings, get_settings
from app.db import get_pool
from app.models import WebhookPayload
from app.services import persistence

logger = structlog.get_logger()
router = APIRouter(prefix="/webhook", tags=["webhook"])

ALLOWED_CHANNELS = {"line", "messenger"}

async def verify_api_key(
    x_api_key: str = Header(...), 
    settings: Settings = Depends(get_settings)
) -> None:
    if x_api_key != settings.conversations_webhook_secret:
        logger.warning("Invalid API key attempt", 
                       received=x_api_key[:5] + "...", 
                       expected=settings.conversations_webhook_secret[:5] + "...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid API Key"
        )

async def process_webhook_events(
    payload: WebhookPayload, 
    pool: asyncpg.Pool, 
    redis: ArqRedis, 
    raw_payload: dict[str, Any]
) -> None:
    for event in payload.events:
        log = logger.bind(event_id=event.id, type=event.type)
        
        # 1. Idempotency check & log event
        await persistence.insert_webhook_event(pool, event, raw_payload)
        
        if event.type != "conversation:message" or not event.payload.message or not event.payload.conversation:
            log.debug("Skipping invalid or non-message event")
            continue
            
        msg_payload = event.payload.message
        conv_payload = event.payload.conversation
        if msg_payload.source.type not in ALLOWED_CHANNELS:
            log.debug("Skipping unsupported channel", channel=msg_payload.source.type)
            continue
            
        if msg_payload.author.type != "user":
            log.debug("Skipping non-user message", author_type=msg_payload.author.type)
            continue
            
        if msg_payload.content.type != "text":
            log.debug("Skipping non-text content", content_type=msg_payload.content.type)
            continue
            
        log.info("Processing valid message event", 
                 conv_id=conv_payload.id,
                 text=msg_payload.content.text)
        
        # 2. Update conversation state
        await persistence.upsert_conversation(pool, event)
        
        # 3. Save message
        await persistence.insert_message(pool, event)
        
        # 4. Add to buffer
        await persistence.insert_message_buffer(pool, event)
        
        # 5. Enqueue debounced flush
        await persistence.enqueue_flush(redis, conv_payload.id)

@router.post("/conversations", status_code=status.HTTP_200_OK)
async def conversations_webhook(
    request: Request,
    payload: WebhookPayload,
    background_tasks: BackgroundTasks,
    _auth: None = Depends(verify_api_key)
) -> dict[str, str]:
    # We need the raw payload for logging/idempotency
    raw_payload = await request.json()
    pool = get_pool()
    redis = request.app.state.redis
    
    background_tasks.add_task(process_webhook_events, payload, pool, redis, raw_payload)
    return {"status": "accepted"}
