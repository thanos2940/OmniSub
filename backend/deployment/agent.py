"""
Vertex AI Agent Engine Entry Point

Defines the root agent for deployment to Google Cloud.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adk_agents import create_translation_pipeline

PROJECT_NAME = os.getenv("PROJECT_NAME", "Omnisub_Deployment")
TARGET_LANGUAGE = os.getenv("TARGET_LANGUAGE", "English")

root_agent = create_translation_pipeline(
    project_name=PROJECT_NAME,
    target_language=TARGET_LANGUAGE,
    glossary={"terms": []}
)
