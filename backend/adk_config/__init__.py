"""
ADK Configuration Module

Exports shared ADK services and factories for Omnisub application.
"""

from .runner_factory import OmnisubRunnerFactory
from .session_manager import OmnisubSessionManager
from .session_service import get_session_service, get_ephemeral_session_service
from .memory_service import get_memory_service

adk_runner_factory = OmnisubRunnerFactory()
adk_session_manager = OmnisubSessionManager()

__all__ = [
    "OmnisubRunnerFactory",
    "OmnisubSessionManager",
    "get_session_service",
    "get_ephemeral_session_service",
    "get_memory_service",
    "adk_runner_factory",
    "adk_session_manager",
]
