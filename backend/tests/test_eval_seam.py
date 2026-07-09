import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from eval.run_eval import translate_items

@pytest.mark.asyncio
async def test_translate_items_uses_generate_seam():
    """Test that translate_items uses the unified generate dispatch."""
    items = [{"source": "Line 1\nLine 2", "reference": "Ref"}]
    model = "local/test-model"
    
    with patch("adk_agents.llm_factory.generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = "1| Trans 1\n2| Trans 2"
        
        updated_items = await translate_items(items, model, "Greek")
        
        assert updated_items[0]["hypothesis"] == "Trans 1\nTrans 2"
        mock_gen.assert_called_once()
        # Verify model name was passed correctly
        assert mock_gen.call_args[1]["model_name"] == model
