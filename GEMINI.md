# OmbiSub - GEMINI.md

This file provides guidance to AI agents (like Gemini) when working with code in this repository. It serves as instructional context for future interactions.

## Project Overview

**OmbiSub** is a context-aware AI subtitle translator built originally for Google's Agent Development Kit (ADK) Seminar. It translates subtitles by building a comprehensive "Spherical Context" (glossary, character genders, lore, rules) before translating a single line, ensuring consistent terminology and tone across entire series.

The project is structured as a monorepo consisting of:
1. **Backend (`/backend`)**: A Python FastAPI server orchestrating Google's Gemini models via the Agent Development Kit (ADK).
2. **Frontend (`/frontend`)**: A React 18 web application built with Vite and styled with Tailwind CSS ("Cozy" design system).
3. **Desktop App (`/electron-app`)**: An Electron wrapper for packaging the application as a standalone desktop executable.

## Building and Running

### Environment Setup
Create a `.env` file in the **project root** (NOT in `backend/`):
```env
GOOGLE_API_KEY=your_gemini_api_key_here
```

### Backend (Python / FastAPI)
The backend uses Python 3.11+ and relies heavily on async programming and ADK agents.

```bash
cd backend
python -m venv .venv
# Activate the virtual environment:
#   Windows: .venv\Scripts\activate
#   Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
- API Docs are available at `http://localhost:8000/docs`

### Frontend (React / Vite)
The frontend uses React 18, React Router v7, Framer Motion, and Tailwind CSS.

```bash
cd frontend
npm install
npm run dev
```
- Development server runs at `http://localhost:5173`

### Desktop App (Electron)
```bash
cd electron-app
npm install
npm run build:win  # For Windows executable
```

### Docker
A `docker-compose.yml` is provided for containerized execution:
```bash
docker-compose up -d
```

## Architecture and Key Files

### Backend Architecture
- **`backend/main.py`**: The FastAPI application defining all endpoints. It orchestrates project management, background tasks (`jobs` dictionary), and delegates complex AI operations to ADK agents.
- **`backend/adk_agents/`**: Contains specialized AI agents (e.g., `cartographer_agent.py` for extraction, `translator_agent.py` for translation). Uses the ADK `Runner` and `SequentialAgent` patterns.
- **`backend/utils/storage.py`**: File-based storage manager. Projects are saved hierarchically under `backend/projects/`.
- **`backend/utils/srt_parser.py`**: Critical utility for handling fragile subtitle timecodes.

### Frontend Architecture
- **`frontend/src/App.jsx` & `main.jsx`**: Application entry points and routing.
- **`frontend/src/components/`**: React components. Key ones include `EditorView.jsx` (side-by-side editing) and `JobProgressWidget.jsx` (real-time job tracking).
- **`frontend/src/api.js`**: Axios HTTP client wrapping all backend endpoints.

## Development Conventions & Best Practices

1. **Subtitle Parsing**: NEVER manually parse or manipulate SRT timecodes using regex or string splitting. Always use the functions provided in `backend/utils/srt_parser.py` (`parse_srt`, `extract_text_only`, `reconstruct_srt`).
2. **AI Jobs**: All AI operations (glossary generation, context enhancement, translation) must be executed as background tasks via the internal Job System (`create_job`, `update_job` in `main.py`) to avoid API timeouts.
3. **ADK Usage**: When adding new AI capabilities, follow the established ADK pattern:
   - Create a specialized `Agent` in `backend/adk_agents/`.
   - Wrap it in a high-level function in `operations.py`.
   - Manage state using the configured `SessionService`.
4. **Data Storage**: State is persistent but file-based. Project metadata, glossaries, and context guides are stored in `project.json`. Episode text and metadata are stored in `data.json` and `metadata.json` respectively.
5. **Glossaries**: Avoid duplicating terms. The Cartographer agent logic must only extract and add NEW terms to the existing project glossary.
6. **Styling (Frontend)**: Follow the "Cozy" UI aesthetic (Glassmorphism, dark mode optimized, smooth Framer Motion transitions). Use Tailwind CSS for utility classes.
7. **Error Handling**: Return structured HTTP exceptions in the backend (`raise HTTPException(...)`). The frontend handles these generically via toasts or modals.

## AI Task Flow Example
When translating:
1. Load project context (`context_guide`) and `glossary`.
2. Extract lines from target episode.
3. Chunk into semantic scenes (via AST builder).
4. Run chunks through `translator_agent.py` using shared session instructions.
5. Parse the returned JSON payload and merge back into the SRT structure.
