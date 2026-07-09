"""
ADK Migration Verification Tests

Tests the instantiation and basic functionality of the new ADK components.
"""

import pytest
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from adk_agents.cartographer_agent import create_cartographer_agent
from adk_agents.translator_agent import create_translator_agent
from adk_agents.translation_pipeline import create_translation_pipeline
from adk_config.runner_factory import OmnisubRunnerFactory
from adk_config.session_manager import OmnisubSessionManager

@pytest.mark.asyncio
async def test_agent_creation():
    """Verify agents can be created with default configuration."""
    cartographer = create_cartographer_agent()
    assert cartographer.name == "CartographerAgent"
    
    translator = create_translator_agent(glossary={"terms": []})
    assert translator.name == "TranslatorAgent"

@pytest.mark.asyncio
async def test_pipeline_creation():
    """Verify translation pipeline creation."""
    pipeline = create_translation_pipeline(
        project_name="TestProject",
        target_language="Spanish",
        glossary={"terms": []}
    )
    assert pipeline.name == "TranslationPipeline_TestProject"
    # Should have 2 sub-agents (Cartographer + Translator)
    assert len(pipeline.sub_agents) == 2

@pytest.mark.asyncio
async def test_session_manager():
    """Verify session manager can create sessions."""
    manager = OmnisubSessionManager()
    # We mock the session service to avoid DB creation in unit tests
    # But for this smoke test, we'll just check the object exists
    assert manager.session_service is not None

@pytest.mark.asyncio
async def test_runner_factory():
    """Verify runner factory creates runners."""
    factory = OmnisubRunnerFactory()
    agent = create_cartographer_agent()
    runner = factory.create_runner(agent, "test_session")
    assert runner.agent == agent
    # session_id is not stored on runner instance in this version
    # assert runner.session_id == "test_session"
