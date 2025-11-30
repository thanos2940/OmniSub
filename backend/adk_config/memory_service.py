"""
ADK Memory Service Configuration

Provides long-term memory for cross-project glossary knowledge.
Enables features like auto-suggesting terms from related projects.
"""

from google.adk.memory import InMemoryMemoryService

_memory_service = None


def get_memory_service() -> InMemoryMemoryService:
    """
    Get singleton instance of the ADK memory service.
    
    Currently uses in-memory storage (lost on restart).
    Production: Switch to VertexAiMemoryBankService for persistence.
    
    Example production configuration:
        from google.adk.memory import VertexAiMemoryBankService
        return VertexAiMemoryBankService(
            project_id="your-gcp-project",
            location="us-central1"
        )
    """
    global _memory_service
    if _memory_service is None:
        _memory_service = InMemoryMemoryService()
    return _memory_service
