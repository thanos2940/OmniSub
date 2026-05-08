# Graph Report - .  (2026-05-08)

## Corpus Check
- 88 files · ~55,243 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 499 nodes · 748 edges · 41 communities (35 shown, 6 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 65 edges (avg confidence: 0.81)
- Token cost: 61,727 input · 98,344 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Backend Main|Backend Main]]
- [[_COMMUNITY_Adk Agents|Adk Agents]]
- [[_COMMUNITY_Backend Main|Backend Main]]
- [[_COMMUNITY_Components Src|Components Src]]
- [[_COMMUNITY_Adk Config|Adk Config]]
- [[_COMMUNITY_Utils Storage|Utils Storage]]
- [[_COMMUNITY_Backend Electron|Backend Electron]]
- [[_COMMUNITY_Sample Ombisub|Sample Ombisub]]
- [[_COMMUNITY_Adk Agents|Adk Agents]]
- [[_COMMUNITY_App Main|App Main]]
- [[_COMMUNITY_Cache Utils|Cache Utils]]
- [[_COMMUNITY_Deployment Docker|Deployment Docker]]
- [[_COMMUNITY_Jsx Frontend|Jsx Frontend]]
- [[_COMMUNITY_Token Utils|Token Utils]]
- [[_COMMUNITY_Json Backend|Json Backend]]
- [[_COMMUNITY_Runner Info|Runner Info]]
- [[_COMMUNITY_Cli Agent|Cli Agent]]
- [[_COMMUNITY_Backend Evaluate|Backend Evaluate]]
- [[_COMMUNITY_Utils Llm|Utils Llm]]
- [[_COMMUNITY_Tools Glossary|Tools Glossary]]
- [[_COMMUNITY_Tools Srt|Tools Srt]]
- [[_COMMUNITY_Electron App|Electron App]]
- [[_COMMUNITY_Fix Backend|Fix Backend]]
- [[_COMMUNITY_Glossary Utils|Glossary Utils]]
- [[_COMMUNITY_Glossary Ombisub|Glossary Ombisub]]
- [[_COMMUNITY_Deployment Agent|Deployment Agent]]
- [[_COMMUNITY_Electron App|Electron App]]
- [[_COMMUNITY_Npm|Npm]]
- [[_COMMUNITY_Pip|Pip]]
- [[_COMMUNITY_Git|Git]]

## God Nodes (most connected - your core abstractions)
1. `React 18` - 23 edges
2. `Backend` - 15 edges
3. `create_job()` - 14 edges
4. `validate_api_key()` - 13 edges
5. `update_job()` - 12 edges
6. `_process_pipeline()` - 12 edges
7. `generate_glossary_adk()` - 12 edges
8. `Frontend` - 12 edges
9. `_process_simple_pipeline()` - 11 edges
10. `create_cartographer_agent()` - 11 edges

## Surprising Connections (you probably didn't know these)
- `test_project_translation()` --calls--> `create_translator_agent()`  [INFERRED]
  backend/main.py → backend/adk_agents/translator_agent.py
- `merge_episode_translation()` --calls--> `reconstruct_srt()`  [INFERRED]
  backend/main.py → backend/utils/srt_parser.py
- `_process_auto_translate()` --calls--> `generate_glossary_adk()`  [INFERRED]
  backend/main.py → backend/adk_agents/operations.py
- `download_single_episode()` --calls--> `reconstruct_srt()`  [INFERRED]
  backend/main.py → backend/utils/srt_parser.py
- `_process_create_glossary()` --calls--> `generate_glossary_adk()`  [INFERRED]
  backend/main.py → backend/adk_agents/operations.py

## Communities (41 total, 6 thin omitted)

### Community 0 - "Backend Main"
Cohesion: 0.05
Nodes (51): ApiKeyRequest, AutoPipelineRequest, batch_translate(), BatchDownloadRequest, BatchTranslateRequest, clear_episode_translation(), ConfirmContextRequest, ConfirmGlossaryRequest (+43 more)

### Community 1 - "Adk Agents"
Cohesion: 0.06
Nodes (46): _build_instruction(), create_cartographer_agent(), Cartographer Agent - Glossary Term Extraction  Extracts translatable terms fro, Create Cartographer Agent for glossary term extraction.          Args:, create_glossary_orchestrator(), Glossary Orchestrator - Research + Extraction Pipeline  Combines ResearchAgent, Create orchestrator combining research and extraction agents.          Workflo, ADK Agents Package  Provides ADK-based agents for OmbiSub translation workflow (+38 more)

### Community 2 - "Backend Main"
Cohesion: 0.06
Nodes (53): get_recommended_chunk_size(), is_local_model(), Check whether a model name refers to a local LLM., Return a safe translation chunk size (in subtitle lines) for this model., enhance_context_guide_adk(), Perform web research for a project using ADK ResearchAgent.      Returns:, Transform research findings into a concise translation style guide., research_project_adk() (+45 more)

### Community 3 - "Components Src"
Cohesion: 0.08
Nodes (14): EpisodeView(), JobProgressWidget(), STAGES, ProjectDetail(), TABS, DEFAULT_LINES, JobContext, JobProvider() (+6 more)

### Community 4 - "Adk Config"
Cohesion: 0.06
Nodes (28): ADK Configuration Module  Exports shared ADK services and factories for OmbiSu, get_memory_service(), ADK Memory Service Configuration  Provides long-term memory for cross-project, Get singleton instance of the ADK memory service.          Currently uses in-m, OmbiSubRunnerFactory, ADK Runner Factory  Creates configured Runners for executing agents with share, Factory for creating ADK Runners with standard OmbiSub configuration., Create a runner for an agent with full service integration.                  A (+20 more)

### Community 5 - "Utils Storage"
Cohesion: 0.06
Nodes (37): Resolve the base URL for the local LLM server., _resolve_local_base_url(), list_all_models(), list_local_models(), Unified model registry — returns Gemini cloud models and discovered local models, Fetch available models from a local OpenAI-compatible server., create_project(), delete_episode() (+29 more)

### Community 6 - "Backend Electron"
Cohesion: 0.07
Nodes (28): aiosqlite, Backend, FastAPI, google-adk, google-cloud-aiplatform, google-generativeai, litellm, openai (+20 more)

### Community 7 - "Sample Ombisub"
Cohesion: 0.12
Nodes (3): INITIAL_GLOSSARY, MOCK_EDITOR_LINES, MOCK_FILES

### Community 8 - "Adk Agents"
Cohesion: 0.18
Nodes (11): backend/adk_agents/, Agent Development Kit (ADK), Backend, FastAPI, Google Gemini Models, AI Job System, backend/main.py, requirements.txt (+3 more)

### Community 9 - "App Main"
Cohesion: 0.2
Nodes (6): { app, BrowserWindow, ipcMain }, path, { spawn }, Store, main(), run_command()

### Community 10 - "Cache Utils"
Cohesion: 0.29
Nodes (9): compute_fingerprint(), _get_client(), get_or_create_cache(), invalidate_cache(), Cache Manager — Gemini Context Caching for OmbiSub  Manages explicit Gemini co, Get a configured Gemini client., Compute a deterministic hash of glossary + context guide.          Used to det, Clear cached content references from project metadata.          Call this when (+1 more)

### Community 11 - "Deployment Docker"
Cohesion: 0.22
Nodes (9): .github/workflows/deploy-frontend.yml, OmbiSub Deployment Guide, Development Setup, Docker, Docker Compose, Docker Deployment, frontend/dist/, GitHub Actions (+1 more)

### Community 12 - "Jsx Frontend"
Cohesion: 0.22
Nodes (8): frontend/src/api.js, frontend/src/App.jsx, frontend/src/components/, Cozy UI Aesthetic, Frontend, frontend/src/main.jsx, Tailwind CSS, Vite

### Community 13 - "Token Utils"
Cohesion: 0.36
Nodes (7): get_project_token_summary(), estimate_glossary_tokens(), estimate_tokens(), get_base_instruction_tokens(), Estimates tokens for the glossary as it would be formatted in a prompt., Estimated tokens for the static TranslatorAgent instructions., Heuristic token estimator (characters / 4).     Adds a 15% buffer for safety and

### Community 14 - "Json Backend"
Cohesion: 0.25
Nodes (7): backend/projects/, data.json, .env file, GOOGLE_API_KEY, metadata.json, ombisub_sessions.db, project.json

### Community 15 - "Runner Info"
Cohesion: 0.46
Nodes (8): Event, google.adk.runners.Runner, RunConfig, Runner.run, types.Content, google.adk.runners, google.adk.runners.types, Runner.run_async

### Community 16 - "Cli Agent"
Cohesion: 0.29
Nodes (7): ADK CLI, backend/deployment/.agent_engine_config.json, backend/deployment/agent.py, gcloud CLI, Google Secret Manager, Production Deployment (Vertex AI), Vertex AI

### Community 18 - "Utils Llm"
Cohesion: 0.4
Nodes (5): parse_translations_from_text(), LLM Utilities - Shared helpers for LLM response processing., Parse translated lines from AI response.      Supports (in priority order):, Strip internal reasoning blocks that local thinking models emit.      Handles, strip_reasoning_blocks()

### Community 19 - "Tools Glossary"
Cohesion: 0.33
Nodes (5): merge_glossaries(), Glossary Tools for ADK Agents  Provides validation and utility functions for g, Validate glossary structure and required fields.          Args:         gloss, Merge new glossary terms into existing glossary, avoiding duplicates., validate_glossary()

### Community 20 - "Tools Srt"
Cohesion: 0.33
Nodes (5): extract_text_from_srt(), parse_srt_content(), SRT Processing Tools for ADK Agents  Wraps SRT parser functions as ADK Functio, Parse SRT subtitle content into structured data.          Args:         conte, Extract plain text from parsed subtitle data.          Args:         subtitle

### Community 21 - "Electron App"
Cohesion: 0.33
Nodes (6): Electron App, electron-app/dist/OmbiSub Setup.exe, electron-store, Node.js 18+, Python 3.11+, Windows Desktop App Deployment

### Community 22 - "Fix Backend"
Cohesion: 0.5
Nodes (4): fix_project(), main(), Fix corrupted project.json files  This script finds and repairs project.json f, Attempt to fix a corrupted project.json file.

### Community 23 - "Glossary Utils"
Cohesion: 0.5
Nodes (3): enforce_glossary(), Glossary Enforcer — Two-Pass CPU-Side Consistency Check  After LLM translation,, Apply glossary consistency corrections to translated subtitle lines.      For ea

### Community 24 - "Glossary Ombisub"
Cohesion: 0.67
Nodes (3): Glossary, OmbiSub Application, Spherical Context

## Knowledge Gaps
- **180 isolated node(s):** `INITIAL_GLOSSARY`, `MOCK_FILES`, `MOCK_EDITOR_LINES`, `Fix corrupted project.json files  This script finds and repairs project.json f`, `Attempt to fix a corrupted project.json file.` (+175 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Backend` connect `Adk Agents` to `Deployment Docker`, `Jsx Frontend`, `Json Backend`, `Electron App`, `Glossary Ombisub`?**
  _High betweenness centrality (0.303) - this node is a cross-community bridge._
- **Why does `FastAPI` connect `Adk Agents` to `Backend Main`?**
  _High betweenness centrality (0.292) - this node is a cross-community bridge._
- **Why does `Frontend` connect `Jsx Frontend` to `Glossary Ombisub`, `Components Src`, `Deployment Docker`, `Electron App`?**
  _High betweenness centrality (0.214) - this node is a cross-community bridge._
- **What connects `INITIAL_GLOSSARY`, `MOCK_FILES`, `MOCK_EDITOR_LINES` to the rest of the system?**
  _180 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Backend Main` be split into smaller, more focused modules?**
  _Cohesion score 0.05 - nodes in this community are weakly interconnected._
- **Should `Adk Agents` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._
- **Should `Backend Main` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._