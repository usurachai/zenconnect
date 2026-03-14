import asyncpg
import httpx
import structlog
from typing import Literal, Optional
from app.config import get_settings

logger = structlog.get_logger()

HANDOFF_KEYWORDS = [
    "talk to human", "human agent", "real person",
    "คุยกับเจ้าหน้าที่", "ขอคุยกับคน", "คุยกับคน",
    "โอนสาย", "พนักงาน"
]

RETURN_TO_AI_KEYWORDS = [
    "back to ai", "ai agent", "use ai",
    "กลับไปคุยกับ ai", "คุยกับ ai", "ให้ ai ตอบ"
]

def detect_handoff_intent(text: str) -> Optional[Literal["human", "ai"]]:
    text_lower = text.lower()
    if any(k in text_lower for k in HANDOFF_KEYWORDS):
        return "human"
    if any(k in text_lower for k in RETURN_TO_AI_KEYWORDS):
        return "ai"
    return None

async def post_zendesk_internal_note(subdomain: str, ticket_id: str, token: str, body: str) -> None:
    # This uses the Zendesk Tickets API
    # Note: ticket_id is needed. We might need to map conversation_id to ticket_id.
    # SunCo usually creates a ticket or you can find it.
    # For now, let's assume we have it or we'll find it via external_id.
    pass

async def execute_handoff_to_human(pool: asyncpg.Pool, conversation_id: str, app_id: str) -> None:
    settings = get_settings()
    log = logger.bind(conversation_id=conversation_id)
    
    # 1. Update DB
    await pool.execute(
        "UPDATE conversations SET agent_mode = 'human', human_requested_at = NOW() WHERE conversation_id = $1",
        conversation_id
    )
    
    # 2. Send farewell to customer via SunCo
    farewell = "กำลังโอนสายให้เจ้าหน้าที่สักครู่ครับ... ⏳"
    sunco_url = f"https://{settings.zendesk_subdomain}.zendesk.com/sc/v2/apps/{app_id}/conversations/{conversation_id}/messages"
    
    async with httpx.AsyncClient() as client:
        await client.post(
            sunco_url,
            auth=(settings.sunco_key_id, settings.sunco_key_secret),
            json={
                "author": {"type": "business"},
                "content": {"type": "text", "text": farewell}
            }
        )
    
    # 3. Notify Zendesk Agents (Internal Note / Ticket Assignment)
    # TODO: Implementation depends on how conversation maps to ticket
    log.info("Handoff to human executed")

async def execute_return_to_ai(pool: asyncpg.Pool, conversation_id: str, app_id: str) -> None:
    settings = get_settings()
    log = logger.bind(conversation_id=conversation_id)
    
    await pool.execute(
        "UPDATE conversations SET agent_mode = 'ai' WHERE conversation_id = $1",
        conversation_id
    )
    
    confirmation = "AI Assistant กลับมาดูแลแล้วครับ 🤖"
    sunco_url = f"https://{settings.zendesk_subdomain}.zendesk.com/sc/v2/apps/{app_id}/conversations/{conversation_id}/messages"
    
    async with httpx.AsyncClient() as client:
        await client.post(
            sunco_url,
            auth=(settings.sunco_key_id, settings.sunco_key_secret),
            json={
                "author": {"type": "business"},
                "content": {"type": "text", "text": confirmation}
            }
        )
    log.info("Return to AI executed")
