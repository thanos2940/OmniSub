import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from routers.health import full_health

@pytest.mark.asyncio
async def test_health_api_key_local_provider():
    """Test that api_key is 'warn' not 'fail' when provider is local."""
    mock_config = {
        "ai_provider": "local",
        "local_llm_base_url": "http://localhost:1234/v1"
    }
    
    with patch("utils.storage.load_global_config", return_value=mock_config), \
         patch("routers.health.has_api_key", return_value=False), \
         patch("routers.health._sample_media_dir", return_value=""), \
         patch("routers.health.shutil.disk_usage", return_value=MagicMock(free=10**10)), \
         patch("routers.health.sonarr_test", new_callable=AsyncMock), \
         patch("routers.health.radarr_test", new_callable=AsyncMock), \
         patch("services.background_worker.worker.running_task", MagicMock(done=lambda: False)), \
         patch("utils.translation_queue.TranslationQueue.get_summary", return_value={}):
        
        # Mock local LLM test (I'll need to add this to health.py)
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            mock_get.return_value.json.return_value = {"data": []}
            
            res = await full_health()
            
            # API key should be warn
            assert res["api_key"]["status"] == "warn"
            assert "ok for local provider" in res["api_key"]["detail"].lower()

@pytest.mark.asyncio
async def test_health_local_llm_reachability():
    """Test that local LLM reachability is checked."""
    mock_config = {
        "ai_provider": "hybrid",
        "local_llm_base_url": "http://localhost:1234/v1"
    }
    
    with patch("utils.storage.load_global_config", return_value=mock_config), \
         patch("routers.health.has_api_key", return_value=True), \
         patch("routers.health._sample_media_dir", return_value=""), \
         patch("routers.health.shutil.disk_usage", return_value=MagicMock(free=10**10)), \
         patch("routers.health.sonarr_test", new_callable=AsyncMock), \
         patch("routers.health.radarr_test", new_callable=AsyncMock), \
         patch("services.background_worker.worker.running_task", MagicMock(done=lambda: False)), \
         patch("utils.translation_queue.TranslationQueue.get_summary", return_value={}):
        
        # Test 1: Reachable
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            mock_get.return_value.json.return_value = {"data": [{"id": "mistral"}]}
            
            res = await full_health()
            assert res["local_llm"]["status"] == "pass"
            assert "mistral" in res["local_llm"]["detail"]
            
        # Test 2: Unreachable
        with patch("httpx.AsyncClient.get", side_effect=Exception("Connection refused")):
            res = await full_health()
            assert res["local_llm"]["status"] == "fail"
