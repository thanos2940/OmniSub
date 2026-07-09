"""
ADK Runner Factory

Creates configured Runners for executing agents with shared services.
Ensures consistent configuration across all agent executions.
"""

from google.adk.runners import Runner
from google.adk.agents import Agent
from .session_service import get_session_service
from .memory_service import get_memory_service

APP_NAME = "Omnisub"


class OmnisubRunnerFactory:
    """
    Factory for creating ADK Runners with standard Omnisub configuration.
    
    All runners share:
    - Session service for project state persistence
    - Memory service for cross-project knowledge
    - Consistent app name for session scoping
    """
    
    def __init__(self):
        self.session_service = get_session_service()
        self.memory_service = get_memory_service()
    
    def create_runner(self, agent: Agent, session_id: str) -> Runner:
        """
        Create a runner for an agent with full service integration.
        
        Args:
            agent: ADK agent to execute
            session_id: Session identifier for state persistence
            
        Returns:
            Configured Runner instance
        """
        return Runner(
            agent=agent,
            app_name=f"{APP_NAME}_{session_id}",
            session_service=self.session_service,
            memory_service=self.memory_service
        )
