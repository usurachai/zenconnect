import asyncpg
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from app.db import get_pool
from app.services import handoff

router = APIRouter(prefix="/handoff", tags=["handoff"])

@router.get("/{conversation_id}/status")
async def get_handoff_status(conversation_id: str, pool: asyncpg.Pool = Depends(get_pool)) -> dict[str, Any]:
    row = await pool.fetchrow(
        "SELECT agent_mode, human_requested_at FROM conversations WHERE conversation_id = $1",
        conversation_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return dict(row)

@router.post("/{conversation_id}/human")
async def handoff_manual_human(
    conversation_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, str]:
    row = await pool.fetchrow("SELECT app_id FROM conversations WHERE conversation_id = $1", conversation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    await handoff.execute_handoff_to_human(pool, conversation_id, row['app_id'])
    return {"status": "success", "mode": "human"}

@router.post("/{conversation_id}/ai")
async def handoff_manual_ai(
    conversation_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, str]:
    row = await pool.fetchrow("SELECT app_id FROM conversations WHERE conversation_id = $1", conversation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    await handoff.execute_return_to_ai(pool, conversation_id, row['app_id'])
    return {"status": "success", "mode": "ai"}
