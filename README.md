# OmbiSub

OmbiSub is a context-aware AI subtitle translator designed to overcome the limitations of line-by-line machine translation. By building a comprehensive "Spherical Context" (including a dynamic glossary, character profiles, and lore guides) before translating a single line, OmbiSub ensures consistent terminology, accurate character voices, and natural dialogue flow across entire series or movies. It effectively solves the problem of "amnesiac" translations where names, genders, and context are frequently lost or mistranslated by standard LLM prompts.

## Architecture Overview

OmbiSub is a monorepo consisting of three main layers:

- **Backend (Python / FastAPI):** The core engine that handles subtitle parsing, state management, and orchestrates Google's Agent Development Kit (ADK). It manages background tasks and API integrations (e.g., Bazarr).
- **Frontend (React / Vite):** A modern, responsive user interface styled with Tailwind CSS, featuring a side-by-side translation editor and real-time job progress tracking.
- **Desktop App (Electron - Optional):** An Electron wrapper that packages the frontend and backend into a standalone, distributable desktop application.

**How they connect:**
The React frontend communicates with the FastAPI backend via RESTful endpoints. The backend utilizes specialized ADK agents to process subtitle data and streams progress updates back to the UI. The optional Electron layer simply serves the React build and manages the underlying Python server lifecycle for desktop users.

## Key Technical Decisions

- **Multi-Agent Architecture:** Instead of a single massive prompt, OmbiSub splits tasks into specialized agents (Cartographer for extraction, Researcher for lore, Translator for localization). This improves accuracy, reduces hallucinations, and makes debugging easier.
- **Context Caching:** Subtitles are processed in chunks to fit context windows efficiently. By ordering prompt sections from most-stable (episode lore) to least-stable (the current line), OmbiSub maximizes Gemini's implicit cache hits, saving API costs and latency.
- **SequentialAgent for Glossary Pipeline:** Generating a project glossary requires multiple steps (extracting terms, filtering duplicates, defining context). Using the ADK `SequentialAgent` pattern ensures these steps execute reliably in order without complex manual orchestration.

## Setup Instructions

### Prerequisites

- Python 3.11+
- Node.js (v18+)
- Google Gemini API Key

### Environment Setup

Create a `.env` file in the **project root**:

```env
GOOGLE_API_KEY=your_gemini_api_key_here
```

### Running Locally

**1. Backend**

```bash
cd backend
python -m venv .venv
# Activate the virtual environment (Windows: .venv\Scripts\activate | Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**2. Frontend**

```bash
cd frontend
npm install
npm run dev
```

The frontend will be available at `http://localhost:5173`.

## Current Status & Roadmap

**Status: Work in Progress (WIP)**
OmbiSub is currently in active development. The core translation pipeline, context extraction, and dynamic glossary generation are functional. The side-by-side editor and basic Bazarr integration are working but being refined.
