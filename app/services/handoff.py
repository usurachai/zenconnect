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

async def execute_handoff_to_human(conn: asyncpg.Connection | asyncpg.Pool, conversation_id: str, app_id: str) -> None:
    settings = get_settings()
    log = logger.bind(conversation_id=conversation_id)
    
    # 1. Update DB
    await conn.execute(
        "UPDATE conversations SET agent_mode = 'human', human_requested_at = NOW() WHERE conversation_id = $1",
        conversation_id
    )
    
    # 2. Send farewell to customer via SunCo
    farewell = "กำลังโอนสายให้เจ้าหน้าที่สักครู่ครับ... ⏳"
    
    from app.services import zendesk
    try:
        await zendesk.send_reply(conversation_id, settings.sunco_app_id, farewell, settings)
    except Exception as e:
        log.error("handoff_reply_failed", error=str(e))
    
    # 3. Notify Zendesk Agents (Internal Note / Ticket Assignment)
    # TODO: Implementation depends on how conversation maps to ticket
    log.info("Handoff to human executed")

async def execute_return_to_ai(conn: asyncpg.Connection | asyncpg.Pool, conversation_id: str, app_id: str) -> None:
    settings = get_settings()
    log = logger.bind(conversation_id=conversation_id)
    
    await conn.execute(
        "UPDATE conversations SET agent_mode = 'ai' WHERE conversation_id = $1",
        conversation_id
    )
    
    confirmation = "AI Assistant กลับมาดูแลแล้วครับ 🤖"
    
    from app.services import zendesk
    try:
        await zendesk.send_reply(conversation_id, settings.sunco_app_id, confirmation, settings)
    except Exception as e:
        log.error("return_to_ai_reply_failed", error=str(e))
        
    log.info("Return to AI executed")
