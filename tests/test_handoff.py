import pytest
from app.services import handoff

def test_detect_handoff_keywords():
    assert handoff.detect_handoff_intent("ขอคุยกับคนหน่อย") == "human"
    assert handoff.detect_handoff_intent("talk to human please") == "human"
    assert handoff.detect_handoff_intent("กลับไปคุยกับ ai") == "ai"
    assert handoff.detect_handoff_intent("Hello how are you") is None

@pytest.mark.asyncio
async def test_handoff_human_updates_db():
    from unittest.mock import MagicMock, AsyncMock, patch
    pool = MagicMock()
    pool.execute = AsyncMock()
    
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=201)
        await handoff.execute_handoff_to_human(pool, "conv_123", "app_123")
        
        # Verify DB update
        pool.execute.assert_called_once()
        args = pool.execute.call_args[0]
        assert "agent_mode = 'human'" in args[0]
        assert args[1] == "conv_123"
        
        # Verify SunCo notification
        mock_post.assert_called_once()
        assert "กำลังโอนสาย" in mock_post.call_args[1]["json"]["content"]["text"]
