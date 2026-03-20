import asyncpg
import httpx
import structlog
from typing import Literal, Optional
from app.config import Settings, get_settings

logger = structlog.get_logger()

HANDOFF_KEYWORDS = [
    "talk to human",
    "human agent",
    "real person",
    "คุยกับเจ้าหน้าที่",
    "ขอคุยกับคน",
    "คุยกับคน",
    "โอนสาย",
    "พนักงาน",
]

RETURN_TO_AI_KEYWORDS = ["back to ai", "ai agent", "use ai", "กลับไปคุยกับ ai", "คุยกับ ai", "ให้ ai ตอบ"]


def detect_handoff_intent(text: str) -> Optional[Literal["human", "ai"]]:
    text_lower = text.lower()
    if any(k in text_lower for k in HANDOFF_KEYWORDS):
        return "human"
    if any(k in text_lower for k in RETURN_TO_AI_KEYWORDS):
        return "ai"
    return None


async def post_zendesk_internal_note(
    conversation_id: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Notify human agents by finding the linked ticket and assigning it with an internal note."""
    from app.services import zendesk

    log = logger.bind(conversation_id=conversation_id)
    try:
        ticket_id = await zendesk.find_ticket_by_conversation_id(conversation_id, settings, client=client)
        if ticket_id is None:
            log.warning("handoff_notify_no_ticket_found")
            return
        await zendesk.assign_ticket(
            ticket_id,
            settings,
            group_id=settings.zendesk_agent_group_id,
            priority="high",
            internal_note="ลูกค้าร้องขอการสนทนากับเจ้าหน้าที่ กรุณาตรวจสอบการสนทนานี้",
            tags=["handoff_requested"],
            client=client,
        )
        log.info("handoff_zendesk_notified", ticket_id=ticket_id)
    except Exception as e:
        log.error("handoff_notify_failed", error=str(e))


async def execute_handoff_to_human(
    conn: asyncpg.Connection | asyncpg.Pool,
    conversation_id: str,
    app_id: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    settings = get_settings()
    log = logger.bind(conversation_id=conversation_id)

    # 1. Update DB
    await conn.execute(
        "UPDATE conversations SET agent_mode = 'human', human_requested_at = NOW() WHERE conversation_id = $1",
        conversation_id,
    )

    # 2. Send farewell to customer via SunCo
    farewell = "กำลังโอนสายให้เจ้าหน้าที่สักครู่ครับ... ⏳"

    from app.services import zendesk

    try:
        await zendesk.send_reply(conversation_id, app_id, farewell, settings, client=client)
    except Exception as e:
        log.error("handoff_reply_failed", error=str(e))

    # 3. Notify Zendesk agents via Support Tickets API
    try:
        await post_zendesk_internal_note(conversation_id, settings, client=client)
    except Exception as e:
        log.error("handoff_notify_unexpected_error", error=str(e))

    log.info("Handoff to human executed")


async def execute_return_to_ai(
    conn: asyncpg.Connection | asyncpg.Pool,
    conversation_id: str,
    app_id: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    settings = get_settings()
    log = logger.bind(conversation_id=conversation_id)

    await conn.execute(
        "UPDATE conversations SET agent_mode = 'ai' WHERE conversation_id = $1", conversation_id
    )

    confirmation = "AI Assistant กลับมาดูแลแล้วครับ 🤖"

    from app.services import zendesk

    try:
        await zendesk.send_reply(conversation_id, app_id, confirmation, settings, client=client)
    except Exception as e:
        log.error("return_to_ai_reply_failed", error=str(e))

    log.info("Return to AI executed")
