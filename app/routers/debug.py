import asyncpg
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from app.db import get_pool
from app.config import get_settings

router = APIRouter(prefix="/debug", tags=["debug"])

def check_debug_enabled() -> None:
    if get_settings().env == "production":
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")

@router.get("/postgres", dependencies=[Depends(check_debug_enabled)])
async def debug_postgres(pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    tables = ["tenants", "conversations", "messages", "message_buffer", "webhook_events"]
    counts = {}
    for table in tables:
        count = await pool.fetchval(f"SELECT COUNT(*) FROM {table}")
        counts[table] = count
    
    # Also get recent conversations
    recent_convs = await pool.fetch("SELECT * FROM conversations ORDER BY created_at DESC LIMIT 5")
    
    return {
        "counts": counts,
        "recent_conversations": [dict(r) for r in recent_convs]
    }

@router.get("/conversation/{conversation_id}", dependencies=[Depends(check_debug_enabled)])
async def debug_conversation(conversation_id: str, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    conv = await pool.fetchrow("SELECT * FROM conversations WHERE conversation_id = $1", conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    messages = await pool.fetch("SELECT * FROM messages WHERE conversation_id = $1 ORDER BY received_at DESC", conversation_id)
    buffer = await pool.fetch("SELECT * FROM message_buffer WHERE conversation_id = $1", conversation_id)
    
    return {
        "conversation": dict(conv),
        "messages": [dict(m) for m in messages],
        "buffer": [dict(b) for b in buffer]
    }
