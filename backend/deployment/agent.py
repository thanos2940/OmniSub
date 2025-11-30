"""
Vertex AI Agent Engine Entry Point

This file defines the root agent for deployment.
"""

from adk_agents.translation_pipeline import create_translation_pipeline
import os

# Environment variables will be injected by Agent Engine
PROJECT_NAME = os.getenv("PROJECT_NAME", "OmbiSub_Deployment")
TARGET_LANGUAGE = os.getenv("TARGET_LANGUAGE", "English")

# Create the root agent (Translation Pipeline)
# Note: In deployment, we might not have access to the local DB session initially,
# so we start with an empty glossary or fetch it from a cloud source.
root_agent = create_translation_pipeline(
    project_name=PROJECT_NAME,
    target_language=TARGET_LANGUAGE,
    glossary={"terms": []}
)
