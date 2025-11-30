"""
ADK Memory Service Configuration

This provides long-term memory for glossaries across projects.
Unlike Sessions (per-project), Memory persists knowledge globally.

Example use case:
- User creates glossary for "Frieren" show
- Memory stores character/term knowledge
- User creates new project for "Frieren Movie"
- Memory automatically suggests relevant terms from the show
"""

from google.adk.memory import InMemoryMemoryService
# For production: from google.adk.memory import VertexAiMemoryBankService

def get_memory_service():
    """
    Get the ADK memory service for OmbiSub.
    
    Currently uses in-memory storage (lost on restart).
    For production, switch to VertexAiMemoryBankService.
    """
    return InMemoryMemoryService()
    
    # Production version:
    # return VertexAiMemoryBankService(
    #     project_id="your-gcp-project",
    #     location="us-central1"
    # )
