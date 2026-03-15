import asyncpg
import structlog
from typing import Any
from arq.connections import RedisSettings
from arq import func
from app.config import get_settings
from app.services import persistence

logger = structlog.get_logger()

AI_DISCLAIMER = "สวัสดีครับ ผมคือ AI Assistant ของ Kasikorn Bank ยินดีที่ได้ดูแลคุณครับ 🤖\n\n"


async def flush_buffer(ctx: dict[str, Any], conversation_id: str) -> None:
    settings = get_settings()
    pool: asyncpg.Pool = ctx["pool"]
    log = logger.bind(conversation_id=conversation_id)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Lock conversation and check mode
            conv = await conn.fetchrow(
                "SELECT * FROM conversations WHERE conversation_id = $1 FOR UPDATE", conversation_id
            )

            if not conv:
                log.warning("Conversation not found during flush")
                return

            if conv["agent_mode"] == "human":
                log.info("Conversation is in human mode, skipping AI reply")
                return

            # 2. Check if we already replied recently (debounce for webhook re-sends)
            # If last_replied_at is within the debounce window, skip to prevent duplicate responses
            if conv["last_replied_at"]:
                import datetime

                elapsed = (
                    datetime.datetime.now(datetime.timezone.utc) - conv["last_replied_at"]
                ).total_seconds()
                if elapsed < settings.flush_buffer_debounce_seconds:
                    log.info("Skipping - recently replied", elapsed_seconds=elapsed)
                    return

            # 3. Get and clear buffered messages atomically
            # Use DELETE RETURNING to prevent duplicate processing from concurrent jobs
            rows = await conn.fetch(
                "DELETE FROM message_buffer WHERE conversation_id = $1 RETURNING body",
                conversation_id,
            )

            if not rows:
                log.info("No messages in buffer")
                return

            buffer_text = "\n".join([r["body"] for r in rows])
            log.info("Flushing buffer", text=buffer_text)

            # 3. Keyword detection for handoff
            from app.services import handoff

            intent = handoff.detect_handoff_intent(buffer_text)
            if intent == "human":
                await handoff.execute_handoff_to_human(conn, conversation_id, conv["app_id"])
                await conn.execute(
                    "DELETE FROM message_buffer WHERE conversation_id = $1", conversation_id
                )
                return
            elif intent == "ai":
                await handoff.execute_return_to_ai(conn, conversation_id, conv["app_id"])
                await conn.execute(
                    "DELETE FROM message_buffer WHERE conversation_id = $1", conversation_id
                )
                return

            # 4. Prepare combined query and fetch history
            history = await persistence.get_conversation_history(conn, conversation_id)

            # 5. Call RAG service
            from app.services import rag

            try:
                answer = await rag.ask(buffer_text, history, settings)
            except Exception as e:
                log.error("RAG service call failed", error=str(e))
                raise  # ARQ will retry

            # 6. Prepare reply with disclaimer if first message
            final_reply = answer
            if not conv["is_first_msg_sent"]:
                final_reply = AI_DISCLAIMER + answer

            # 7. Save AI reply to database
            await persistence.insert_outbound_message(conn, conversation_id, final_reply)

            # 8. Reply via Zendesk Conversations API
            from app.services import zendesk

            try:
                await zendesk.send_reply(
                    conversation_id, settings.sunco_app_id, final_reply, settings
                )
            except Exception as e:
                log.error("Zendesk reply failed", error=str(e))
                raise  # ARQ will retry

            # 9. Success: update state
            # Buffer was already deleted at the start (DELETE RETURNING)
            await conn.execute(
                "UPDATE conversations SET is_first_msg_sent = TRUE, last_replied_at = NOW() WHERE conversation_id = $1",
                conversation_id,
            )

            log.info("Successfully replied and cleared buffer")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    ctx["pool"] = await asyncpg.create_pool(dsn=settings.database_url)
    logger.info("Worker started up")


async def shutdown(ctx: dict[str, Any]) -> None:
    pool: asyncpg.Pool = ctx["pool"]
    await pool.close()
    logger.info("Worker shutting down")


class WorkerSettings:
    functions = [func(flush_buffer, keep_result=0)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
