# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OmbiSub is a context-aware AI subtitle translator built for Google's Agent Development Kit (ADK) Seminar. The system builds comprehensive glossaries and context guides before translating, ensuring consistent character genders, terminology, and tone across entire series.

**Core Concept**: "Spherical Context" - build complete understanding (WHO/WHAT/WHERE/HOW/WHY) before translating a single line.

## Development Commands

### Backend (Python/FastAPI)

```bash
cd backend

# Install dependencies (use virtual environment)
pip install -r requirements.txt

# Run development server
uvicorn main:app --reload

# API documentation at: http://localhost:8000/docs
```

**Environment Setup**: Create `.env` in project root (NOT in backend/):
```
GOOGLE_API_KEY=your_gemini_api_key_here
```

### Frontend (React/Vite)

```bash
cd frontend

# Install dependencies
npm install

# Run development server (http://localhost:5173)
npm run dev

# Build for production
npm run build

# Preview production build
npm preview
```

## Architecture

### ADK-Based Implementation

The codebase is built entirely with Google's Agent Development Kit (ADK):

- **ADK Agents**: `backend/adk_agents/` - All AI agents use ADK framework
  - Production-ready with automatic retries, session management, structured output
  - CartographerAgent, ResearchAgent, TranslatorAgent
  - SequentialAgent orchestration for multi-step workflows

- **ADK Configuration**: `backend/adk_config/`
  - DatabaseSessionService for persistent state
  - OmbiSubSessionManager for project-specific operations
  - OmbiSubRunnerFactory for agent initialization

**All endpoints use ADK agents exclusively** - no legacy code remains.

### AI Agent System

Three specialized ADK agents handle different phases:

1. **CartographerAgent** (`adk_agents/cartographer_agent.py`)
   - Purpose: Glossary term extraction with structured output
   - Tools: None (pure extraction)
   - Output: Pydantic `GlossaryOutput` schema with terms
   - Model: `gemini-flash-latest`

2. **ResearchAgent** (`adk_agents/research_agent.py`)
   - Purpose: Web research for canonical information
   - Tools: `google_search` (built-in ADK tool)
   - Process: Search → Validate → Extract official names/translations
   - Model: `gemini-flash-latest`

3. **TranslatorAgent** (`adk_agents/translator_agent.py`)
   - Purpose: Context-aware translation with glossary enforcement
   - Configuration: Temperature 0.3 for consistency
   - Features: Glossary context injection, case sensitivity, batch processing
   - Model: `gemini-flash-latest`

**Orchestration**:
- `GlossaryOrchestrator`: SequentialAgent(ResearchAgent → CartographerAgent)
- `TranslationPipeline`: SequentialAgent(CartographerAgent → TranslatorAgent)

**High-Level Operations** (`adk_agents/operations.py`):
- `generate_glossary_adk()`: Wrapper for glossary creation
- `research_project_adk()`: Wrapper for web research
- `translate_batch_adk()`: Wrapper for batch translation
- `enhance_context_guide_adk()`: Transform research into translation instructions

### Storage Architecture

**Hierarchical File Structure**:
```
backend/projects/
└── {ProjectName}/
    ├── project.json          # Glossary, context guide, settings
    └── episodes/
        └── {EpisodeName}/
            ├── data.json     # Translated subtitle data
            ├── original.srt  # Original file
            └── metadata.json # Season, line counts, status
```

**Session Management** (ADK):
- `ombisub_sessions.db`: SQLite database for ADK session state
- Managed by `adk_config/session_manager.py`
- Provides transactional safety vs. manual JSON writes

**Key Storage Functions** (`utils/storage.py`):
- `create_project()`, `load_project_metadata()`, `save_project_metadata()`
- `save_episode()`, `load_episode()`, `delete_episode()`
- `list_projects()`, `list_episodes()`

### SRT Handling

**Critical**: Subtitle timecodes are fragile. Use dedicated parser.

**Functions** (`utils/srt_parser.py`):
- `parse_srt(content)`: SRT string → List[{index, timecode, text}]
- `extract_text_only(data)`: Strips timecodes for AI processing
- `reconstruct_srt(data)`: Reassembles SRT with translated text

**Workflow**: Parse → Extract text → Send to AI → Reconstruct → Save

### FastAPI Endpoints

**API Structure** (`main.py`):

- Project Management: `/projects`, `/projects/{name}`, `/projects/{name}/import`
- Episodes: `/projects/{name}/episodes`, `/projects/{name}/episodes/{episode}/upload`
- AI Operations:
  - `/projects/{name}/glossary/create` - Web research mode (no files needed)
  - `/projects/{name}/glossary/enhance` - Extract terms from episodes
  - `/projects/{name}/context/create` - Generate tone/style guide
  - `/projects/{name}/episodes/{episode}/translate` - Single episode
  - `/projects/{name}/batch-translate` - Multiple episodes with shared cache
- Job Tracking: `/jobs/{job_id}` - Real-time status, logs, AI responses

**Background Jobs**:
All AI operations run as background tasks tracked in `jobs: Dict[str, JobStatus]`.
Use `create_job()`, `update_job()`, `jobs[job_id]` pattern for async operations.

### Frontend Architecture

**Tech Stack**: React 18 + Vite + Tailwind CSS + Framer Motion + React Router v7

**Key Components** (`frontend/src/components/`):
- `ProjectList.jsx`, `ProjectDetail.jsx` - Project/episode management
- `GlossaryEditor.jsx`, `GlossaryReviewModal.jsx` - Glossary CRUD + approval UI
- `EditorView.jsx` - Side-by-side original/translated subtitle editor
- `JobProgressWidget.jsx` - Real-time job tracking with logs
- `AILogPanel.jsx` - Debug view for prompts and AI responses

**API Client** (`frontend/src/api.js`): Axios wrapper for backend communication

**Context**: `frontend/src/context/JobContext.jsx` - Global job state management

## Key Workflows

### Glossary Building (3 Modes)

1. **Research Mode**: AI searches web for show info (no subtitle files needed)
   - Endpoint: `POST /projects/{name}/glossary/create`
   - Prompt includes show name → Google Search → Character/location extraction

2. **Analysis Mode**: Extract terms from uploaded episode files
   - Endpoint: `POST /projects/{name}/glossary/enhance`
   - SRT → Parse → Extract text → NER → Web research → New terms only

3. **Enhancement Mode**: Add NEW terms to existing glossary (no duplicates)
   - Deduplication logic in cartographer
   - Frontend shows GlossaryReviewModal for approval

### Translation Process

1. **Context Caching**: Create reusable Gemini context with glossary + guide
   - Cache TTL: 60 minutes
   - Stored in `project.json`: `context_cache_name`, `context_cache_expiry`
   - Cost savings: ~50% for batch operations

2. **Batch Translation**:
   - Endpoint: `POST /projects/{name}/batch-translate`
   - Shares single context cache across all episodes
   - Auto-chunks files >350 lines (prevents token limits)

3. **Quality Control**:
   - GlossaryReviewModal: Approve/reject new terms before merging
   - EditorView: Line-by-line editing with manual overrides
   - AI responses logged for debugging

### Parent/Child Projects

**Hierarchy**: Parent projects (e.g., "Fate Universe") → Child seasons (e.g., "Fate/Zero")

**Import Functionality**: `POST /projects/{name}/import`
- Share glossaries between related projects
- Useful for multi-season shows with consistent terminology

## Data Structures

### Glossary Format

```json
{
  "terms": [
    {
      "term": "Winterfell",
      "translation": "Γουίντερφελ",
      "gender": "neutral",
      "description": "Ancestral castle of House Stark",
      "case_sensitive": true,
      "keep_original": false
    }
  ]
}
```

**Fields**:
- `gender`: "male", "female", "neutral" (affects grammar in gendered languages)
- `case_sensitive`: Enforce exact case matching during translation
- `keep_original`: Don't translate this term (e.g., fantasy proper nouns)

### Episode Data Format

```json
[
  {
    "index": 1,
    "timecode": "00:00:01,000 --> 00:00:03,500",
    "text": "Translated subtitle text"
  }
]
```

### Project Metadata

```json
{
  "show_name": "Game of Thrones",
  "target_language": "Greek",
  "glossary": {...},
  "context_guide": "Fantasy medieval setting. Use formal register...",
  "parent_project": null,
  "type": "show",
  "context_cache_name": "cached-content-xyz",
  "context_cache_expiry": "2025-11-30T15:30:00Z",
  "settings": {
    "scan_model": "gemini-flash-lite-latest",
    "translation_model": "gemini-flash-latest"
  }
}
```

## Google ADK Integration

**Seminar Requirements**: Demonstrate 3+ ADK concepts
**OmbiSub Demonstrates**: 7 concepts ✅

**Implemented Concepts**:
1. **Multi-Agent System**: 3 specialized agents + 2 SequentialAgent orchestrators
2. **Built-in Tools**: Google Search integration in ResearchAgent
3. **Sessions**: DatabaseSessionService with SQLite backend (`ombisub_sessions.db`)
4. **Memory**: InMemoryMemoryService (production-ready for VertexAiMemoryBankService)
5. **Structured Output**: Pydantic schemas (`GlossaryOutput`) for type-safe responses
6. **Runner Pattern**: OmbiSubRunnerFactory with session/memory service injection
7. **Observability**: Job tracking with prompts, responses, errors, and progress

**Configuration Files**:
- `adk_config/session_service.py`: DatabaseSessionService setup
- `adk_config/runner_factory.py`: Runner factory with service injection
- `adk_config/session_manager.py`: Project-level session operations

**Production Deployment**:
- Designed for Vertex AI Agent Builder deployment
- Session database can migrate to cloud storage
- Memory service ready for VertexAiMemoryBankService

## Common Tasks

### Add New AI Feature

1. Create ADK agent in `adk_agents/`:
   ```python
   from google.adk.agents import Agent
   from google.adk.models.google_llm import Gemini

   def create_my_agent(model_name: str = "gemini-flash-latest") -> Agent:
       return Agent(
           name="MyAgent",
           model=Gemini(model=model_name, retry_options=RETRY_CONFIG),
           instruction="...",
           tools=[],  # Add tools if needed
           output_key="my_result"
       )
   ```

2. Add high-level operation in `adk_agents/operations.py`:
   ```python
   async def my_operation_adk(...) -> Tuple[Dict, Dict]:
       agent = create_my_agent(model_name=model_name)
       runner = Runner(agent=agent, app_name="OmbiSub")
       response = await runner.run(prompt)
       return result, debug_info
   ```

3. Export in `adk_agents/__init__.py`

4. Create FastAPI endpoint in `main.py`:
   ```python
   @app.post("/projects/{name}/my-feature")
   async def my_feature(name: str, background_tasks: BackgroundTasks):
       job_id = create_job("my_feature")
       background_tasks.add_task(_process_my_feature, job_id, name)
       return {"job_id": job_id}
   ```

5. Add job polling in frontend (`api.js` + component)

### Debug Translation Issues

1. Check API key: `cat .env` (must be in project root)
2. Check glossary: Review `projects/{name}/project.json` for conflicting terms
3. Check AI logs: `GET /jobs/{job_id}` → `prompt` and `ai_response` fields
4. Check context cache: Verify `context_cache_expiry` hasn't passed
5. Frontend: Use AILogPanel component to view prompts/responses

### Handle SRT Encoding Issues

- All files must be UTF-8 encoded
- Check timecode format: `HH:MM:SS,MMM --> HH:MM:SS,MMM`
- Use `parse_srt()` validation before processing
- If parsing fails, check for BOM or non-UTF8 characters

## Design Patterns

### "Cozy" UI Aesthetic

- Glassmorphism with `backdrop-blur`
- Dark mode optimized (purple/blue gradients)
- Smooth Framer Motion animations
- Real-time progress feedback
- Minimal clicks for common operations

### Error Handling

**Backend**: Return structured errors:
```python
raise HTTPException(status_code=400, detail={
    "error": "error_code",
    "message": "Human-readable message"
})
```

**Frontend**: Display errors in modals or toasts with clear actions

### State Management

- **Global**: JobContext for background task tracking
- **Local**: Component state for UI interactions
- **Persistent**: Backend storage + ADK sessions

## Important Constraints

1. **Never corrupt timecodes**: Always use `srt_parser.py` functions
2. **Context cache TTL**: 60 minutes (recreate if expired)
3. **Batch size limit**: 350 lines per chunk (prevents token overflow)
4. **API key location**: `.env` in project root, NOT in backend/
5. **File naming**: Exported SRTs append language codes (e.g., `_el.srt` for Greek)
6. **Glossary deduplication**: CartographerAgent only adds NEW terms (no duplicates)

## Deployment Options

### Docker (Recommended)

```bash
# Build and run
docker-compose up -d

# Access at http://localhost:8000
```

### Desktop Application

```bash
cd electron-app
npm install

# Windows
npm run build:win

# macOS
npm run build:mac

# Linux
npm run build:linux
```

### Production (Vertex AI)

The ADK agents are designed for deployment to Google Cloud Vertex AI Agent Builder:

1. Configure `deployment/agent.py` with production settings
2. Deploy using ADK CLI: `adk deploy agent_engine --project=PROJECT_ID`
3. Agents run serverless with automatic scaling
