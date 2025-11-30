"""
Session Manager for OmbiSub Projects

Maps OmbiSub projects to ADK sessions for state persistence.
Handles session creation, retrieval, and state updates.
"""

from google.adk.sessions import Session
from .session_service import get_session_service
from typing import Dict, Optional

APP_NAME = "OmbiSub"
DEFAULT_USER = "default_user"


class OmbiSubSessionManager:
    """
    Manages ADK sessions for OmbiSub projects.
    
    Each project gets a unique session storing:
    - glossary: Term dictionary for translation consistency
    - context_guide: Translation instructions and tone guidance
    - target_language: Output language for translations
    - settings: Project-specific configuration
    """
    
    def __init__(self):
        self.session_service = get_session_service()
    
    def _session_id(self, project_name: str) -> str:
        """Generate consistent session ID for a project."""
        return f"ombisub_project_{project_name}"
    
    async def get_or_create_project_session(
        self, 
        project_name: str,
        initial_metadata: Optional[Dict] = None
    ) -> Session:
        """
        Get existing session or create new one for a project.
        
        Args:
            project_name: Name of the project
            initial_metadata: Initial state for new projects
            
        Returns:
            ADK Session object with project state
        """
        session_id = self._session_id(project_name)
        
        try:
            session = await self.session_service.get_session(
                session_id=session_id,
                app_name=APP_NAME,
                user_id=DEFAULT_USER
            )
            if session:
                return session
        except Exception:
            pass
        
        session = await self.session_service.create_session(
            session_id=session_id,
            app_name=APP_NAME,
            user_id=DEFAULT_USER
        )
        
        if initial_metadata:
            session.state.update({
                "project_name": project_name,
                "glossary": initial_metadata.get("glossary", {"terms": []}),
                "context_guide": initial_metadata.get("context_guide", ""),
                "target_language": initial_metadata.get("target_language", "English"),
                "settings": initial_metadata.get("settings", {})
            })
        
        return session
    
    async def update_glossary(self, project_name: str, glossary: Dict):
        """Update glossary in project session state."""
        session = await self.get_or_create_project_session(project_name)
        session.state["glossary"] = glossary
    
    async def update_context(self, project_name: str, context_guide: str):
        """Update context guide in project session state."""
        session = await self.get_or_create_project_session(project_name)
        session.state["context_guide"] = context_guide
    
    async def get_project_state(self, project_name: str) -> Dict:
        """Get complete project state from session."""
        session = await self.get_or_create_project_session(project_name)
        return dict(session.state)
