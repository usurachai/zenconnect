import httpx
import structlog
from app.config import Settings

logger = structlog.get_logger(__name__)

async def send_reply(
    conversation_id: str,
    app_id: str,
    text: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> None:
    """
    Sends a message back to the Zendesk/SunCo conversation.

    URL: https://{subdomain}.zendesk.com/sc/v2/apps/{app_id}/conversations/{conv_id}/messages
    Auth: Basic (INTEGRATION_KEY_ID : INTEGRATION_KEY_SECRET)

    Pass a shared `client` (e.g. from worker context) to reuse TCP connections
    across calls. If omitted, a new client is created and closed for this call.
    """
    url = f"https://{settings.zendesk_subdomain}.zendesk.com/sc/v2/apps/{app_id}/conversations/{conversation_id}/messages"

    payload = {
        "author": {
            "type": "business"
        },
        "content": {
            "type": "text",
            "text": text
        }
    }

    log = logger.bind(conversation_id=conversation_id, app_id=app_id)

    _owned = client is None
    _client = client if client is not None else httpx.AsyncClient(timeout=10.0)
    try:
        response = await _client.post(
            url,
            auth=(settings.integration_key_id, settings.integration_key_secret),
            json=payload
        )
        response.raise_for_status()
        log.info("zendesk_reply_sent_successfully")

    except httpx.HTTPStatusError as e:
        log.error("zendesk_reply_http_error", status_code=e.response.status_code, response_text=e.response.text)
        raise
    except Exception as e:
        log.error("zendesk_reply_client_error", error=str(e))
        raise
    finally:
        if _owned:
            await _client.aclose()
