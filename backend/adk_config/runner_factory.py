"""
ADK Runner Factory

Creates configured Runners for executing agents.
"""

from google.adk.runners import Runner
from google.adk.agents import Agent
from .session_service import get_session_service
from .memory_service import get_memory_service

class OmbiSubRunnerFactory:
    """
    Factory to create ADK Runners with standard configuration.
    
    Ensures all runners have access to:
    - Session service (for project state)
    - Memory service (for global knowledge)
    - Standard observability hooks
    """
    
    def __init__(self):
        self.session_service = get_session_service()
        self.memory_service = get_memory_service()
    
    def create_runner(self, agent: Agent, session_id: str) -> Runner:
        """
        Create a runner for a specific agent and session.
        
        Args:
            agent: The ADK agent to run
            session_id: The session ID to attach to
            
        Returns:
            Configured Runner instance
        """
        return Runner(
            agent=agent,
            app_name="OmbiSub",
            session_service=self.session_service,
            memory_service=self.memory_service
            # session_id is passed to run() methods, not __init__
            # plugins=[LoggingPlugin()]
        )
