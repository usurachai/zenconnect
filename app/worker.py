import httpx
import asyncpg
import structlog
from typing import Any
from arq.connections import RedisSettings
from app.config import get_settings

logger = structlog.get_logger()

AI_DISCLAIMER = "สวัสดีครับ ผมคือ AI Assistant ของ Kasikorn Bank ยินดีที่ได้ดูแลคุณครับ 🤖\n\n"

async def flush_buffer(ctx: dict[str, Any], conversation_id: str) -> None:
    settings = get_settings()
    pool: asyncpg.Pool = ctx['pool']
    log = logger.bind(conversation_id=conversation_id)
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Lock conversation and check mode
            conv = await conn.fetchrow(
                "SELECT * FROM conversations WHERE conversation_id = $1 FOR UPDATE",
                conversation_id
            )
            
            if not conv:
                log.warning("Conversation not found during flush")
                return
                
            if conv['agent_mode'] == 'human':
                log.info("Conversation is in human mode, skipping AI reply")
                return
            
            # 2. Get buffered messages
            rows = await conn.fetch(
                "SELECT body FROM message_buffer WHERE conversation_id = $1 ORDER BY created_at ASC",
                conversation_id
            )
            
            if not rows:
                log.info("No messages in buffer")
                return
                
            buffer_text = "\n".join([r['body'] for r in rows])
            log.info("Flushing buffer", text=buffer_text)
            
            # 3. Keyword detection for handoff
            from app.services import handoff
            intent = handoff.detect_handoff_intent(buffer_text)
            if intent == "human":
                await handoff.execute_handoff_to_human(pool, conversation_id, conv['app_id'])
                await conn.execute("DELETE FROM message_buffer WHERE conversation_id = $1", conversation_id)
                return
            elif intent == "ai":
                await handoff.execute_return_to_ai(pool, conversation_id, conv['app_id'])
                await conn.execute("DELETE FROM message_buffer WHERE conversation_id = $1", conversation_id)
                return

            # 4. Call RAG service
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    rag_resp = await client.post(
                        f"{settings.rag_base_url}/v1/ask",
                        json={"query": buffer_text, "conversation_id": conversation_id}
                    )
                    rag_resp.raise_for_status()
                    answer = rag_resp.json().get("answer", "ขออภัยครับ ผมไม่สามารถประมวลผลคำตอบได้ในขณะนี้")
            except Exception as e:
                log.error("RAG service call failed", error=str(e))
                raise # ARQ will retry
                
            # 4. Prepare reply with disclaimer if first message
            final_reply = answer
            if not conv['is_first_msg_sent']:
                final_reply = AI_DISCLAIMER + answer
                
            # 5. Reply via Zendesk Conversations API
            app_id = conv['app_id']
            sunco_url = f"https://{settings.zendesk_subdomain}.zendesk.com/sc/v2/apps/{app_id}/conversations/{conversation_id}/messages"
            
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        sunco_url,
                        auth=(settings.sunco_key_id, settings.sunco_key_secret),
                        json={
                            "author": {"type": "business"},
                            "content": {"type": "text", "text": final_reply}
                        }
                    )
                    resp.raise_for_status()
            except Exception as e:
                log.error("Zendesk Conversations API call failed", error=str(e))
                raise # ARQ will retry
                
            # 6. Success: clear buffer and update state
            await conn.execute(
                "DELETE FROM message_buffer WHERE conversation_id = $1",
                conversation_id
            )
            
            await conn.execute(
                "UPDATE conversations SET is_first_msg_sent = TRUE, last_replied_at = NOW() WHERE conversation_id = $1",
                conversation_id
            )
            
            log.info("Successfully replied and cleared buffer")

async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    ctx['pool'] = await asyncpg.create_pool(dsn=settings.database_url)
    logger.info("Worker started up")

async def shutdown(ctx: dict[str, Any]) -> None:
    pool: asyncpg.Pool = ctx['pool']
    await pool.close()
    logger.info("Worker shutting down")

class WorkerSettings:
    functions = [flush_buffer]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
