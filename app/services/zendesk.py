import httpx
import structlog
from typing import Any
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


async def find_ticket_by_conversation_id(
    conversation_id: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Find a Zendesk Support ticket linked to a SunCo conversation ID.

    Uses the Search API: GET /api/v2/search.json?query=fieldvalue:{conversation_id}
    Auth: Basic ({email}/token : {api_token})
    Returns the ticket ID string if found, None otherwise.
    """
    url = f"https://{settings.zendesk_subdomain}.zendesk.com/api/v2/search.json"
    auth_user = f"{settings.zendesk_email}/token"
    log = logger.bind(conversation_id=conversation_id)

    _owned = client is None
    _client = client if client is not None else httpx.AsyncClient(timeout=10.0)
    try:
        response = await _client.get(
            url,
            params={"query": f"fieldvalue:{conversation_id}"},
            auth=(auth_user, settings.zendesk_api_token),
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if results:
            ticket_id = str(results[0]["id"])
            log.info("zendesk_ticket_found", ticket_id=ticket_id)
            return ticket_id
        log.info("zendesk_ticket_not_found")
        return None
    except httpx.HTTPStatusError as e:
        log.error("zendesk_search_http_error", status_code=e.response.status_code)
        raise
    except Exception as e:
        log.error("zendesk_search_client_error", error=str(e))
        raise
    finally:
        if _owned:
            await _client.aclose()


async def assign_ticket(
    ticket_id: str,
    settings: Settings,
    *,
    group_id: str | None = None,
    priority: str | None = None,
    internal_note: str | None = None,
    tags: list[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Assign a Zendesk Support ticket to a group with optional priority, tags, and internal note.

    PUT /api/v2/tickets/{ticket_id}
    Auth: Basic ({email}/token : {api_token})
    """
    url = f"https://{settings.zendesk_subdomain}.zendesk.com/api/v2/tickets/{ticket_id}"
    auth_user = f"{settings.zendesk_email}/token"
    log = logger.bind(ticket_id=ticket_id)

    ticket_payload: dict[str, Any] = {}
    if group_id is not None:
        ticket_payload["group_id"] = group_id
    if priority is not None:
        ticket_payload["priority"] = priority
    if tags is not None:
        ticket_payload["tags"] = tags
    if internal_note is not None:
        ticket_payload["comment"] = {"body": internal_note, "public": False}

    _owned = client is None
    _client = client if client is not None else httpx.AsyncClient(timeout=10.0)
    try:
        response = await _client.put(
            url,
            auth=(auth_user, settings.zendesk_api_token),
            json={"ticket": ticket_payload},
        )
        response.raise_for_status()
        log.info("zendesk_ticket_assigned")
    except httpx.HTTPStatusError as e:
        log.error("zendesk_assign_http_error", status_code=e.response.status_code)
        raise
    except Exception as e:
        log.error("zendesk_assign_client_error", error=str(e))
        raise
    finally:
        if _owned:
            await _client.aclose()
