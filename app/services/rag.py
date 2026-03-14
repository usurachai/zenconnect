import httpx
import structlog
from typing import List, Dict
from app.config import Settings

logger = structlog.get_logger(__name__)

async def ask(query: str, history: List[Dict[str, str]], settings: Settings) -> str:
    """
    Calls the meowRAG /api/v1/ask endpoint.
    
    Expected meowRAG Request Body:
    {
        "query": "...",
        "conversation_history": [{"role": "user", "content": "..."}, ...],
        "top_k": 5
    }
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
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
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
