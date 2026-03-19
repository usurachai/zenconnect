import asyncpg
import httpx
import json
import structlog
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo
from arq.connections import RedisSettings
from arq import func
from opentelemetry import trace
from app.config import get_settings
from app.services import persistence, handoff, rag, zendesk
from app.services.working_hours import is_within_working_hours
from app.telemetry import configure_logging, handle_exception, setup_tracing

logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)

AI_DISCLAIMER = "สวัสดีครับ ผมคือ AI Assistant ของ Kasikorn Bank ยินดีที่ได้ดูแลคุณครับ 🤖\n\n"


async def flush_buffer(
    ctx: dict[str, Any],
    conversation_id: str,
    parent_trace_id: str | None = None,
) -> None:
    with tracer.start_as_current_span("worker.flush_buffer") as span:
        span.set_attribute("conversation_id", conversation_id)
        if parent_trace_id:
            span.set_attribute("parent_trace_id", parent_trace_id)

        settings = get_settings()
        pool: asyncpg.Pool = ctx["pool"]
        log = logger.bind(conversation_id=conversation_id, parent_trace_id=parent_trace_id)

        async with pool.acquire() as conn:
            async with conn.transaction():
                # 1. Lock conversation and check mode
                conv = await conn.fetchrow(
                    "SELECT * FROM conversations WHERE conversation_id = $1 FOR UPDATE",
                    conversation_id,
                )

                if not conv:
                    log.warning("Conversation not found during flush")
                    return

                if conv["agent_mode"] == "human":
                    log.info("Conversation is in human mode, skipping AI reply")
                    return

                # 2. Working-hours gate
                tz = ZoneInfo(settings.agent_timezone)
                local_now = datetime.now(tz)
                if not is_within_working_hours(settings, now=local_now):
                    wh_fields = {
                        "local_time": local_now.isoformat(),
                        "timezone": settings.agent_timezone,
                        "weekday": local_now.strftime("%A"),
                        "hour": local_now.hour,
                        "working_days": list(settings.agent_working_days),
                        "hour_start": settings.agent_working_hour_start,
                        "hour_end": settings.agent_working_hour_end,
                    }
                    log.info("outside_working_hours", **wh_fields)
                    span.set_attribute("outside_working_hours", True)
                    span.set_attribute("wh.local_time", local_now.isoformat())
                    span.set_attribute("wh.timezone", settings.agent_timezone)
                    span.set_attribute("wh.weekday", local_now.strftime("%A"))
                    span.set_attribute("wh.hour", local_now.hour)
                    await conn.execute(
                        "DELETE FROM message_buffer WHERE conversation_id = $1",
                        conversation_id,
                    )
                    if settings.agent_outside_hours_reply:
                        span.add_event("outside_hours.auto_reply_sent")
                        log.info("outside_hours.auto_reply_sent", **wh_fields)
                        await zendesk.send_reply(
                            conversation_id,
                            conv["app_id"],
                            settings.agent_outside_hours_reply,
                            settings,
                            client=ctx.get("zendesk_client"),
                        )
                    else:
                        span.add_event("outside_hours.silent_skip")
                        log.info("outside_hours.silent_skip", **wh_fields)
                    return

                span.set_attribute("app_id", conv["app_id"])
                span.set_attribute("channel", conv["channel"])
                span.set_attribute("agent_mode", conv["agent_mode"])
                span.set_attribute("is_first_msg_sent", bool(conv["is_first_msg_sent"]))

                # 3. Get and clear buffered messages atomically
                rows = await conn.fetch(
                    "DELETE FROM message_buffer WHERE conversation_id = $1 RETURNING body",
                    conversation_id,
                )

                if not rows:
                    log.info("No messages in buffer")
                    return

                buffer_text = "\n".join([r["body"] for r in rows])
                log.info("Flushing buffer", text=buffer_text)
                span.set_attribute("buffer_size", len(rows))
                span.set_attribute("buffer_text", buffer_text)

                # 3. Keyword detection for handoff
                intent = handoff.detect_handoff_intent(buffer_text)
                span.set_attribute("handoff_intent", intent or "none")
                if intent == "human":
                    await handoff.execute_handoff_to_human(conn, conversation_id, conv["app_id"])
                    return
                elif intent == "ai":
                    await handoff.execute_return_to_ai(conn, conversation_id, conv["app_id"])
                    return

                # 4. Prepare combined query and fetch history
                history = await persistence.get_conversation_history(conn, conversation_id)

                # 5. Call RAG service
                with tracer.start_as_current_span("rag.ask") as rag_span:
                    rag_span.set_attribute("rag.url", f"{settings.rag_base_url}/api/v1/ask")
                    rag_span.set_attribute("rag.query", buffer_text)
                    rag_span.set_attribute("rag.query_length", len(buffer_text))
                    rag_span.set_attribute("rag.history", json.dumps(history, ensure_ascii=False))
                    rag_span.set_attribute("rag.history_length", len(history))
                    rag_span.set_attribute("rag.top_k", 5)
                    try:
                        answer = await rag.ask(buffer_text, history, settings, client=ctx.get("rag_client"))
                        rag_span.set_attribute("rag.answer", answer)
                        rag_span.set_attribute("rag.answer_length", len(answer))
                    except (httpx.ConnectError, httpx.TimeoutException) as e:
                        handle_exception(rag_span, e)
                        rag_span.add_event("rag.unreachable")
                        log.warning("rag_unreachable", error=str(e))
                        if settings.rag_unavailable_reply:
                            try:
                                await zendesk.send_reply(
                                    conversation_id,
                                    settings.sunco_app_id,
                                    settings.rag_unavailable_reply,
                                    settings,
                                    client=ctx.get("zendesk_client"),
                                )
                            except Exception as zd_err:
                                log.error("rag_unavailable_reply_failed", error=str(zd_err))
                        return
                    except Exception as e:
                        handle_exception(rag_span, e)
                        raise

                # 6. Prepare reply with disclaimer if first message
                final_reply = answer
                if not conv["is_first_msg_sent"]:
                    final_reply = AI_DISCLAIMER + answer

                span.set_attribute("reply_length", len(final_reply))

                # 7. Save AI reply to database
                await persistence.insert_outbound_message(conn, conversation_id, final_reply)

                # 8. Reply via Zendesk Conversations API
                with tracer.start_as_current_span("zendesk.send_reply") as zd_span:
                    zd_span.set_attribute("conversation_id", conversation_id)
                    zd_span.set_attribute("app_id", settings.sunco_app_id)
                    zd_span.set_attribute("reply_length", len(final_reply))
                    try:
                        await zendesk.send_reply(
                            conversation_id, settings.sunco_app_id, final_reply, settings,
                            client=ctx.get("zendesk_client"),
                        )
                    except Exception as e:
                        handle_exception(zd_span, e)
                        raise

                # 9. Success: update state
                await conn.execute(
                    "UPDATE conversations SET is_first_msg_sent = TRUE, last_replied_at = NOW() WHERE conversation_id = $1",
                    conversation_id,
                )

                log.info("Successfully replied and cleared buffer")


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    setup_tracing()
    settings = get_settings()
    ctx["pool"] = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=settings.worker_pool_max_size,
    )
    from arq import create_pool

    ctx["redis"] = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    ctx["rag_client"] = httpx.AsyncClient(timeout=30.0)
    ctx["zendesk_client"] = httpx.AsyncClient(timeout=10.0)
    logger.info("Worker started up")


async def shutdown(ctx: dict[str, Any]) -> None:
    pool: asyncpg.Pool = ctx["pool"]
    redis = ctx.get("redis")
    if redis:
        await redis.aclose()
    await pool.close()
    await ctx["rag_client"].aclose()
    await ctx["zendesk_client"].aclose()
    logger.info("Worker shutting down")


class WorkerSettings:
    functions = [func(flush_buffer, keep_result=0)]
    max_jobs = get_settings().worker_max_jobs
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
