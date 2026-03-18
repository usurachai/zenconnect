import httpx
import structlog
from typing import List, Dict
from app.config import Settings

logger = structlog.get_logger(__name__)

async def ask(
    query: str,
    history: List[Dict[str, str]],
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """
    Calls the meowRAG /api/v1/ask endpoint.

    Expected meowRAG Request Body:
    {
        "query": "...",
        "conversation_history": [{"role": "user", "content": "..."}, ...],
        "top_k": 5
    }

    Pass a shared `client` (e.g. from worker context) to reuse TCP connections
    across calls. If omitted, a new client is created and closed for this call.
    """
    url = f"{settings.rag_base_url}/api/v1/ask"
    headers = {
        "Authorization": f"Bearer {settings.rag_api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "query": query,
        "conversation_history": history,
        "top_k": 5
    }

    log = logger.bind(url=url, query=query[:50])

    _owned = client is None
    _client = client if client is not None else httpx.AsyncClient(timeout=30.0)
    try:
        response = await _client.post(url, headers=headers, json=payload)
        response.raise_for_status()

        data = response.json()
        answer = data.get("answer")
        if not answer:
            log.error("rag_response_missing_answer", response_body=data)
            return "ขออภัยครับ ผมไม่สามารถหาคำตอบที่เหมาะสมได้ในขณะนี้"

        log.info("rag_response_received")
        return str(answer)

    except httpx.HTTPStatusError as e:
        log.error("rag_http_error", status_code=e.response.status_code, error=str(e))
        raise
    except Exception as e:
        log.error("rag_client_error", error=str(e))
        raise
    finally:
        if _owned:
            await _client.aclose()
