"""
Session Manager for OmbiSub Projects

Maps OmbiSub projects to ADK sessions for state persistence.
"""

from google.adk.sessions import Session
from .session_service import get_session_service
from typing import Dict, Optional
import json

class OmbiSubSessionManager:
    """
    Manages ADK sessions for OmbiSub projects.
    
    Each project gets a unique session that stores:
    - Glossary state
    - Context guide
    - Translation history
    - Cache metadata
    """
    
    def __init__(self):
        self.session_service = get_session_service()
    
    async def get_or_create_project_session(
        self, 
        project_name: str,
        initial_metadata: Optional[Dict] = None
    ) -> Session:
        """
        Get existing session for a project or create a new one.
        
        Args:
            project_name: Name of the project
            initial_metadata: Initial project metadata (for new projects)
            
        Returns:
            ADK Session object
        """
        # Session ID format: "ombisub_project_{project_name}"
        session_id = f"ombisub_project_{project_name}"
        
        try:
            # Try to get existing session
            session = await self.session_service.get_session(
                session_id=session_id,
                app_name="OmbiSub",
                user_id="default_user"
            )
            if session is None:
                raise Exception("Session not found (returned None)")
            return session
        except Exception as e:
            print(f"[OmbiSub] get_session failed for {session_id}: {e}")
            
            try:
                # Create new session
                session = await self.session_service.create_session(
                    session_id=session_id,
                    app_name="OmbiSub",
                    user_id="default_user"
                )
                
                if session is None:
                    raise Exception("Failed to create session (returned None)")
                
                # Initialize state with project metadata
                if initial_metadata:
                    session.state.update({
                        "project_name": project_name,
                        "glossary": initial_metadata.get("glossary", {"terms": []}),
                        "context_guide": initial_metadata.get("context_guide", ""),
                        "target_language": initial_metadata.get("target_language", "English"),
                        "settings": initial_metadata.get("settings", {})
                    })
                    # Session state is auto-persisted by DatabaseSessionService
                
                return session
            except Exception as create_error:
                print(f"[OmbiSub] create_session failed: {create_error}")
                # If creation failed because it exists, try getting it again
                # This handles race conditions or spurious get failures
                if "already exists" in str(create_error):
                    return await self.session_service.get_session(
                        session_id=session_id,
                        app_name="OmbiSub",
                        user_id="default_user"
                    )
                raise create_error
    
    async def update_glossary(self, project_name: str, glossary: Dict):
        """Update the glossary in project session state."""
        session = await self.get_or_create_project_session(project_name)
        session.state["glossary"] = glossary
        # Session state is auto-persisted by DatabaseSessionService
    
    async def update_context(self, project_name: str, context_guide: str):
        """Update the context guide in project session state."""
        session = await self.get_or_create_project_session(project_name)
        session.state["context_guide"] = context_guide
        # Session state is auto-persisted by DatabaseSessionService
    
    async def get_project_state(self, project_name: str) -> Dict:
        """Get full project state from session."""
        session = await self.get_or_create_project_session(project_name)
        return dict(session.state)
