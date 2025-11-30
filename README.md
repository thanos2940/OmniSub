
# OmbiSub - Context-Aware AI Subtitle Translator

**Google Seminar Capstone Project**

A sophisticated AI-powered subtitle translation platform that builds "Spherical Context" (comprehensive glossaries, character genders, and lore) before translating, ensuring consistent and context-aware translations across entire series.

---

## 🌟 Overview

OmbiSub revolutionizes subtitle translation by using Google's Gemini AI to understand the **full context** of your media before translating a single line. Unlike traditional subtitle translators that work line-by-line, OmbiSub:

1. **Analyzes** your subtitle files to extract characters, locations, terminology, and lore
2. **Researches** shows using Google Search to verify canonical information
3. **Builds** a comprehensive glossary with gender-aware translations
4. **Caches** context for efficient batch processing
5. **Translates** with full awareness of character relationships, tone, and narrative context

Perfect for anime, TV series, and films with complex terminology, character relationships, or fantasy/sci-fi concepts.

---

## 🏗 Architecture

### System Design

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (React)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ Project Mgmt │  │   Glossary   │  │   Episode   │  │
│  │              │  │    Editor    │  │   Editor    │  │
│  └──────────────┘  └──────────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────────┘
                           │
                    FastAPI REST API
                           │
┌─────────────────────────────────────────────────────────┐
│                  Backend (Python)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ Cartographer │  │  Translator  │  │   Storage   │  │
│  │    Agent     │  │    Agent     │  │   Manager   │  │
│  │              │  │              │  │             │  │
│  │ • Glossary   │  │ • Context    │  │ • Projects  │  │
│  │   Builder    │  │   Caching    │  │ • Episodes  │  │
│  │ • Web        │  │ • Batch      │  │ • Metadata  │  │
│  │   Research   │  │   Translation│  │             │  │
│  └──────┬───────┘  └──────┬───────┘  └─────────────┘  │
│         └─────────────────┴─────────────────┐          │
└─────────────────────────────────────────────┼──────────┘
                                              │
                                    Google Gemini AI
                                    (gemini-flash)
```

### Technology Stack

#### Backend
- **Framework**: FastAPI (Python) - High-performance async API
- **AI Engine**: Google Gemini (gemini-flash-latest, gemini-flash-lite-latest)
- **Storage**: File-based JSON storage with hierarchical project organization
- **Key Libraries**:
  - `google-generativeai` - Gemini API integration with context caching
  - `python-dotenv` - Environment configuration
  - `uvicorn` - ASGI server

#### Frontend
- **Framework**: React 18 with Vite
- **Styling**: Tailwind CSS with custom "Cozy" design system
- **Animations**: Framer Motion for smooth transitions
- **Routing**: React Router v7
- **Icons**: Lucide React
- **HTTP Client**: Axios

---

## 🎓 Google ADK Seminar Alignment

**Capstone Requirement**: Demonstrate at least 3 key ADK concepts  
**OmbiSub Demonstrates**: 5 concepts ✅

This project demonstrates core concepts from Google's Agent Development Kit (ADK) seminar using a custom implementation with the Gemini API. While built with raw Gemini API rather than the ADK framework, the architecture naturally aligns with ADK patterns and best practices.

### Concepts Demonstrated

#### 1. ✅ Multi-Agent System
**ADK Pattern**: Agent-powered by LLM with specialized sub-agents

**OmbiSub Implementation**:
- **CartographerAgent** ([cartographer.py](file:///p:/Tools/OmbiSub%20-%20Google%20Seminar%20Capstone%20Project/backend/agents/cartographer.py)): Specialized in glossary building and context extraction
- **TranslatorAgent** ([translator.py](file:///p:/Tools/OmbiSub%20-%20Google%20Seminar%20Capstone%20Project/backend/agents/translator.py)): Specialized in context-aware translation

Each agent has distinct responsibilities, models, and tools - following the "team of specialists" pattern from Day 1 of the seminar.

#### 2. ✅ Custom Tools
**ADK Pattern**: FunctionTool with clear docstrings and type hints

**OmbiSub Implementation**:
- SRT parser functions: `parse_srt()`, `extract_text_only()`, `reconstruct_srt()`
- Storage manager: `create_project()`, `save_episode()`, `load_episode()`, etc.
- All functions have comprehensive docstrings and type hints per ADK best practices
- Return structured dictionaries for error handling

#### 3. ✅ Built-in Tools (Google Search)
**ADK Pattern**: Integration with `google_search` tool

**OmbiSub Implementation**:
- CartographerAgent uses Google Search tool via `protos.Tool.GoogleSearch()`
- Use cases: Research Mode for show information, canonical name validation, terminology verification

#### 4. ✅ Sessions & State Management
**ADK Pattern**: SessionService for persistent state

**OmbiSub Implementation**:
- File-based project persistence (`backend/projects/{ProjectName}/project.json`)
- Episode-level state tracking (`episodes/{EpisodeName}/data.json`)
- Context cache name/expiry storage in project metadata
- State persists across server restarts

#### 5. ✅ Context Engineering (Caching)
**ADK Pattern**: Context caching and prompt engineering

**OmbiSub Implementation**:
- 60-minute context caching via `caching.CachedContent.create()`
- Reduces API costs by ~50% for batch operations
- Structured system instructions with glossary embedding
- Gender-aware prompts and case-sensitivity flags

### Implementation Mapping

| ADK Concept | OmbiSub Custom Implementation |
|-------------|-------------------------------|
| `Agent(name, model, instruction, tools)` | `CartographerAgent`, `TranslatorAgent` classes |
| `Gemini(model="gemini-2.5-flash-lite")` | `genai.GenerativeModel(model_name)` |
| `tools=[google_search]` | `protos.Tool.GoogleSearch()` |
| `FunctionTool(my_function)` | Custom utility functions in `backend/utils/` |
| `Runner` with `SessionService` | FastAPI endpoints + file-based storage |
| `session.state["key"]` | `project_meta["key"]` in JSON files |
| Context caching | `caching.CachedContent.create()` with TTL |

### Why Custom Implementation?

OmbiSub uses raw Gemini API instead of ADK because:
1. Project requires custom orchestration with FastAPI backend
2. Hierarchical project/episode structure needs specialized storage
3. SRT file handling requires domain-specific utilities
4. Direct control over context caching and model selection

Despite the custom approach, all patterns align with ADK principles and demonstrate the same concepts taught in the seminar.

---

## ✨ Core Features

### 1. **Context-Aware Translation**
- Builds comprehensive glossaries before translation
- Maintains character gender consistency across episodes
- Respects show-specific terminology and lore
- Uses AI-powered web research for canonical accuracy

### 2. **Hierarchical Project Management**
- **Parent Projects**: Organize "universes" (e.g., "Fate/Stay Night", )
- **Season Projects**: Manage individual seasons (e.g., "Season 1", "Season 2")
- **Episode Management**: Track translation status, line counts, and metadata
- **Cross-Project Imports**: Share glossaries/context between related projects

### 3. **AI Agents**

#### Cartographer Agent (`agents/cartographer.py`)
- **Purpose**: Glossary construction and context analysis
- **Capabilities**:
  - Named Entity Recognition (characters, locations, items, concepts)
  - Web research using Google Search tool
  - Gender inference from context
  - Capitalization and case-sensitivity analysis
  - Incremental glossary enhancement (adds only NEW terms)
- **Models**: `gemini-flash-lite-latest` (default)

#### Translator Agent (`agents/translator.py`)
- **Purpose**: Context-aware subtitle translation
- **Capabilities**:
  - Context caching (60-minute TTL for repeated use)
  - Batch processing with automatic chunking (350 lines/chunk)
  - Gender-aware grammar adaptation
  - Glossary term enforcement with case sensitivity
  - Fallback handling for cache expiration
- **Models**: `gemini-flash-latest` (default for translation)

### 4. **Smart Workflow**

#### Glossary Building
1. **Research Mode**: AI uses Google Search to find show information (no subtitles needed)
2. **Analysis Mode**: Extracts terms from subtitle text
3. **Enhancement Mode**: Adds NEW terms to existing glossary (avoids duplicates)
4. **Manual Editing**: Full glossary editor with term management

#### Translation Process
1. **Context Caching**: Creates reusable AI context for the entire project
2. **Batch Translation**: Process multiple episodes in one operation
3. **Automatic Chunking**: Splits large files (>350 lines) for optimal AI processing
4. **Real-time Progress**: Job tracking with detailed logs and AI responses

#### Quality Control
- **Glossary Review Modal**: Approve/reject new terms before merging
- **Context Review Modal**: Review AI-generated context guides
- **Episode Editor**: Line-by-line editing with original/translated side-by-side
- **Manual Overrides**: Edit any translation with full glossary support

### 5. **Batch Operations**
- **Batch Translation**: Translate multiple episodes with shared context
- **Batch Download**: Export selected episodes as SRT files (properly named with language codes)
- **Batch Delete**: Remove multiple episodes
- **Season Tagging**: Organize episodes by season

### 6. **Advanced Features**
- **Dark Mode**: Full dark theme support
- **Job System**: Background task processing with progress tracking
- **AI Debugger**: View prompts and responses for all AI operations
- **File Selection**: Choose specific episodes for glossary enhancement
- **Context Guide Enhancement**: Expand basic guides into detailed translation instructions
- **Language Codes**: Automatic ISO language code appending to filenames

---

## 📋 Complete Workflow

### Initial Setup

```
1. Create Project
   ├─ Set show name and target language
   ├─ Choose type (show/movie/parent)
   └─ Optional: Link to parent project

2. Upload Episodes
   ├─ Drag & drop .srt files
   ├─ Auto-detect season from filename (SxxExx)
   └─ Episodes grouped by season
```

### Building Context (The "Spherical Context")

```
3. Create/Enhance Glossary
   ├─ Research Mode (no files needed)
   │  ├─ AI searches web for show info
   │  ├─ Identifies characters, locations, concepts
   │  └─ Suggests translations in target language
   │
   ├─ Analysis Mode (from episode files)
   │  ├─ Extracts proper nouns and terminology
   │  ├─ Researches canonical spellings/genders
   │  ├─ Adds ONLY new terms (no duplicates)
   │  └─ Updates context guide with tone/style
   │
   └─ Review & Approve
      ├─ See new vs. existing terms
      ├─ Edit translations
      ├─ Set gender/case-sensitivity flags
      └─ Add descriptions

4. Create/Enhance Context Guide
   ├─ AI analyzes show genre and setting
   ├─ Generates tone guidelines
   ├─ Creates grammar inference rules
   ├─ Handles "blind reader" problem (no speaker labels)
   └─ Provides formal/informal register rules
```

### Translation

```
5. Translate Episodes
   ├─ Single Episode
   │  ├─ Optional: Enhance glossary first
   │  ├─ Create context cache (60min)
   │  ├─ Process in chunks if >350 lines
   │  └─ Track progress in real-time
   │
   └─ Batch Translation
      ├─ Select multiple episodes
      ├─ Optional: Enhance glossary with selected files
      ├─ Shared context cache across batch
      └─ Sequential processing with progress

6. Review & Edit
   ├─ Open episode editor
   ├─ View original & translated side-by-side
   ├─ Edit individual lines
   ├─ Mark lines for review
   └─ Save changes

7. Export
   ├─ Download individual episodes
   └─ Batch download (ZIP with language codes)
      Example: "MyShow_S01E01_el.srt" (Greek)
```

---

## 🎯 Key Differentiators

### vs. Traditional Subtitle Translators

| Feature | Traditional | OmbiSub |
|---------|------------|---------|
| **Context Awareness** | Line-by-line (no memory) | Full series context |
| **Character Consistency** | Name variations possible | Locked glossary terms |
| **Gender Handling** | Generic translations | Gender-aware grammar |
| **Terminology** | Inconsistent | Centralized glossary |
| **Batch Processing** | Sequential, no learning | Context caching, learns from batch |
| **Quality Control** | Post-translation fixes | Pre-translation glossary approval |

### The "Spherical Context" Advantage

Traditional subtitle translation fails with complex media because:
1. "He" could refer to multiple characters
2. Fantasy terms get translated differently each time
3. Character names might have canonical spellings
4. Tone/formality varies by scene context

**OmbiSub solves this** by building a complete "sphere" of context:
- **WHO**: Character glossary with genders
- **WHAT**: Terminology for items, concepts, magic systems
- **WHERE**: Location names and their translations
- **HOW**: Tone guide for formal/informal language
- **WHY**: Lore descriptions to guide AI decisions

---

## 🚀 Setup & Installation

### Prerequisites

- **Python** 3.8+ 
- **Node.js** 16+
- **Google Gemini API Key** ([Get one here](https://makersuite.google.com/app/apikey))

### Backend Setup

```bash
cd backend

# Install dependencies
pip install -r requirements.txt

# Configure API key
# Create .env file in project root (not in backend/)
echo "GOOGLE_API_KEY=your_api_key_here" > ../.env

# Start server
uvicorn main:app --reload
```

Backend runs at `http://localhost:8000`

**API Documentation**: `http://localhost:8000/docs`

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

Frontend runs at `http://localhost:5173`

---

## 📚 API Reference

### Project Management
- `GET /projects` - List all projects
- `POST /projects` - Create new project
- `GET /projects/{name}` - Get project details
- `PUT /projects/{name}` - Update project metadata
- `POST /projects/{name}/import` - Import glossary/context from another project

### Episode Management
- `GET /projects/{name}/episodes` - List episodes with translation status
- `POST /projects/{name}/episodes/{episode}/upload` - Upload SRT file
- `GET /projects/{name}/episodes/{episode}` - Get episode data
- `POST /projects/{name}/episodes/{episode}/save` - Save edited subtitles
- `DELETE /projects/{name}/episodes/{episode}` - Delete episode
- `POST /projects/{name}/episodes/{episode}/metadata` - Update metadata

### AI Operations
- `POST /projects/{name}/glossary/create` - Create glossary (web research)
- `POST /projects/{name}/glossary/enhance` - Add new terms to glossary
- `POST /projects/{name}/context/create` - Generate context guide
- `POST /projects/{name}/context/enhance` - Expand context guide
- `POST /projects/{name}/episodes/{episode}/scan` - Scan episode for terms
- `POST /projects/{name}/episodes/{episode}/translate` - Translate single episode
- `POST /projects/{name}/batch-translate` - Translate multiple episodes
- `POST /projects/{name}/batch-download` - Download episodes as ZIP

### Job System
- `GET /jobs/{job_id}` - Get job status, progress, and logs

---

## 📁 Project Structure

```
OmbiSub/
├── backend/
│   ├── agents/
│   │   ├── cartographer.py      # Glossary builder agent
│   │   └── translator.py        # Translation agent
│   ├── utils/
│   │   ├── srt_parser.py        # SRT file parser/reconstructor
│   │   └── storage.py           # File-based storage manager
│   ├── main.py                  # FastAPI application (35+ endpoints)
│   ├── requirements.txt
│   └── projects/                # Data storage
│       └── {ProjectName}/
│           ├── project.json     # Glossary, context, settings
│           └── episodes/
│               └── {EpisodeName}/
│                   ├── data.json
│                   ├── original.srt
│                   └── metadata.json
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── ProjectList.jsx
│   │   │   ├── ProjectDetail.jsx
│   │   │   ├── GlossaryEditor.jsx
│   │   │   ├── EditorView.jsx
│   │   │   └── ... (15+ components)
│   │   ├── context/
│   │   │   └── JobContext.jsx   # Background job tracking
│   │   ├── api.js               # API client
│   │   └── App.jsx
│   ├── package.json
│   └── tailwind.config.js
└── .env                         # GOOGLE_API_KEY
```

---

## 🔧 Configuration

### Project Settings
Each project can configure:
- **Translation Model**: `gemini-flash-latest` (default)
- **Context Model**: `gemini-flash-lite-latest` (default)
- **Glossary Model**: `gemini-flash-lite-latest` (default)
- **Target Language**: Any language supported by Gemini
- **Context Guide**: Manual or AI-generated tone/style instructions

### Environment Variables
```bash
# Required
GOOGLE_API_KEY=your_gemini_api_key

# Optional (backend port)
PORT=8000
```

---

## 🎨 Design Philosophy

OmbiSub follows a "Cozy" design aesthetic:
- **Glassmorphism** and subtle gradients
- **Smooth animations** with Framer Motion
- **Dark mode** optimized for long editing sessions
- **Informative UI** with real-time job progress
- **Minimal clicks** for common workflows

---

## 🧠 AI Model Usage

### Context Caching
- **Purpose**: Reuse glossary/instructions across multiple API calls
- **Benefit**: ~50% cost reduction for batch operations
- **TTL**: 60 minutes (automatically recreated after expiry)
- **Storage**: Stored in project metadata (`context_cache_name`, `context_cache_expiry`)

### Model Selection
- **Cartographer**: Uses `-lite` model (cheaper, good for glossary building)
- **Translator**: Uses full model (better quality for actual translation)
- **Configurable**: Models can be changed per-project in settings

---

## 🐛 Troubleshooting

### API Key Issues
```bash
# Verify .env is in project root (not in backend/)
cat .env  # Should show: GOOGLE_API_KEY=...

# Check backend logs for: "GOOGLE_API_KEY not found"
```

### Translation Errors
- **Check glossary** for conflicting terms
- **Verify target language** is correctly set
- **Review AI response** in job logs for debugging

### File Upload Issues
- Only `.srt` format supported
- File must be UTF-8 encoded
- Ensure timecodes follow SRT format: `HH:MM:SS,MMM --> HH:MM:SS,MMM`

---

## 📝 License

This project is a capstone submission for Google's AI Agent Seminar.

---

## 🙏 Acknowledgments

- **Google Gemini API** for powerful AI capabilities
- **Google Search Tool** for canonical research
- **FastAPI** for excellent async API framework
- **React/Vite** for modern frontend development

