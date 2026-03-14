import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app import worker

@pytest.fixture
def mock_ctx():
    ctx = {}
    pool = MagicMock()
    ctx['pool'] = pool
    
    conn = AsyncMock()
    # Mock pool.acquire() context manager
    pool.acquire.return_value.__aenter__.return_value = conn
    
    # Mock conn.transaction() context manager
    transaction_manager = MagicMock()
    transaction_manager.__aenter__ = AsyncMock()
    transaction_manager.__aexit__ = AsyncMock()
    conn.transaction = MagicMock(return_value=transaction_manager)
    
    return ctx, conn

@pytest.mark.asyncio
async def test_flush_buffer_human_mode_aborts(mock_ctx):
    ctx, conn = mock_ctx
    # Mock conversation record in human mode
    conn.fetchrow.return_value = {"agent_mode": "human"}
    
    await worker.flush_buffer(ctx, "conv_123")
    
    # Check that it selected for update
    conn.fetchrow.assert_called_once()
    assert "SELECT" in conn.fetchrow.call_args[0][0]
    assert "FOR UPDATE" in conn.fetchrow.call_args[0][0]
    
    # Check that no RAG call would have happened (though we haven't mocked it yet)
    # For now, just ensuring it returns early

@pytest.mark.asyncio
async def test_flush_buffer_happy_path(mock_ctx):
    ctx, conn = mock_ctx
    # Mock conversation record in ai mode
    conn.fetchrow.return_value = {
        "agent_mode": "ai", 
        "app_id": "app_123", 
        "is_first_msg_sent": False
    }
    # Mock buffer messages
    conn.fetch.return_value = [{"body": "Hello"}, {"body": "where are you?"}]
    
    with patch("httpx.AsyncClient.post") as mock_post:
        # Mock RAG response
        mock_rag_resp = MagicMock()
        mock_rag_resp.status_code = 200
        mock_rag_resp.json.return_value = {"answer": "I am here!"}
        
        # Mock Conversations API response
        mock_sunco_resp = MagicMock()
        mock_sunco_resp.status_code = 201
        
        mock_post.side_effect = [mock_rag_resp, mock_sunco_resp]
        
        await worker.flush_buffer(ctx, "conv_123")
        
        # Verify RAG called with concatenated text
        assert "Hello\nwhere are you?" in mock_post.call_args_list[0][1]["json"]["query"]
        
        # Verify update and clear buffer calls
        assert any("UPDATE conversations" in str(app[0]) for app in conn.execute.call_args_list)
        assert any("DELETE FROM message_buffer" in str(app[0]) for app in conn.execute.call_args_list)
