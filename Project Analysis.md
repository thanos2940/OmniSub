The Ultimate Guide: Context-Aware Subtitle Translator
=====================================================

**Version:** 4.0 (CAT Tool Edition)**Target System:** Client-Server AI Application

1\. Executive Vision
--------------------

This application solves the "context-blindness" of standard machine translation. By treating subtitle files not as isolated strings but as part of a **Narrative Universe**, the system builds a "Spherical Context" (Glossary of terms, gender, lore) _before_ attempting translation.

**Core Philosophy:**

1.  **Context First:** Never translate without a Glossary.
    
2.  **Fluidity:** The UI must be "Cozy"—calm, animated, and non-intrusive.
    
3.  **Professional Control:** The user is the Editor-in-Chief; the AI is the junior translator.
    

2\. Technical Architecture (The Stack)
--------------------------------------

We are discarding single-script approaches (Streamlit) in favor of a decoupled architecture to support complex state management and fluid UI animations.

### 2.1 Frontend (The "Cozy" Layer)

*   **Framework:** **React 18+ (Vite)**.
    
*   **Styling:** **Tailwind CSS** (Utility-first) + custom CSS for Glassmorphism (backdrop-filter).
    
*   **Animation:** **Framer Motion**. This is non-negotiable for the "smooth, slow" aesthetic.
    
*   **State Management:** **Zustand** or **React Context**. We need to track the state of multiple files (Uploading -> Scanning -> Review -> Translating -> Done).
    

### 2.2 Backend (The "Brain")

*   **Server:** **FastAPI (Python)**. It supports asynchronous operations, essential for handling long-running AI tasks without blocking the UI.
    
*   **AI Orchestration:** **Google Gen AI SDK (Gemini)**.
    
*   **Database:** **SQLite** (Local/MVP) or **Firebase Firestore** (Production) to store Glossaries and Project states.
    
*   **Processing:** Python's native asyncio for parallel processing of multiple files.
    

3\. The "Spherical Context" Workflow
------------------------------------

This is the core algorithm. It distinguishes this project from a simple wrapper.

### Phase 1: Ingestion & Sanitization

**Input:** N number of .srt files.**Logic:**

1.  **Parse:** Convert .srt files into a structured list of objects using a robust regex parser.
    
2.  **Strip:** Separate the Timecodes from the Text. **Critical Safety Step:** The AI never sees timecodes. It only sees arrays of text strings.
    
    *   _Data:_ \[ "Winterfell is ours.", "Open the gate!" \]
        

### Phase 2: The Deep Scan (Context Extraction)

Before translation, we must understand the universe.

1.  **Internal Scan (NER):** Send the raw text arrays of _all_ uploaded files to a fast model (gemini-flash-lite-latest).
    
    *   _Prompt:_ "Extract all proper nouns, unique terminology, and character names from this text. Return JSON."
        
2.  **External Research:**
    
    *   _Action:_ Backend identifies the show (e.g., "Game of Thrones S01").
        
    *   _Tool:_ Google Search / Wiki API.
        
    *   _Query:_ "Game of Thrones character genders and key terms list."
        
3.  **Synthesis:** Merge Internal NER + External Research into a **Master Glossary**. Deduplicate entries.
    

### Phase 3: The Configuration Gate (User Interaction)

*   **Glossary Review:** User edits specific fields: gender, type, keep\_original.
    
*   **System Instructions:** User sets the global tone (e.g., "Use formal Japanese," "Translate for a Greek audience, keeping fantasy terms in English").
    
*   **Auto-Pilot Mode:** If enabled, skips this and uses defaults.
    

### Phase 4: Context-Aware Translation

**Optimization:** We use **Context Caching** (Gemini 1.5 feature).

1.  **Cache Creation:** We upload the _System Instructions_ + _Master Glossary_ **once**. We get a cache\_key.
    
2.  **Batching:** We split the text arrays into chunks of **50 lines**.
    
    *   _Context Overlap:_ Include the last 3 lines of the _previous_ batch in the prompt.
        
3.  **Execution:** Call gemini-flash-latest using the cache\_key.
    

### Phase 5: The Editor (Post-Translation)

**UI Layout:** Vertical Split View (Original vs. Translated).

1.  **Comparison:** User sees both columns (Original Source vs. AI Output).
    
2.  **Manual Edit:** User clicks a cell to edit the translated text directly.
    
3.  **Save & Export:** User saves manual corrections to the project state and exports the final .srt.
    

4\. Data Structures (JSON Schema)
---------------------------------

### 4.1 The Master Glossary (Enhanced)

This is the artifact shared across all files in a project.

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   {    "project_id": "uuid-123",    "system_instructions": "Maintain a medieval fantasy tone. Use formal 'You' (Voi) for royalty.",    "terms": [      {        "term": "Winterfell",        "translation": "Winterfell",        "type": "location",        "keep_original": true,        "target_gender": "n/a",        "notes": "Do not translate."      },      {        "term": "Chair",        "translation": "Καρέκλα",        "type": "item",        "keep_original": false,        "target_gender": "female",        "notes": "Specific to the scene context."      }    ]  }   `

### 4.2 The Editor State

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   {    "lines": [      {        "id": 1,        "timecode": "00:00:10,000 --> 00:00:12,000",        "original": "Winter is coming.",        "translated": "O Inverno está chegando.",        "is_edited": false,        "needs_review": false      }    ]  }   `

5\. UI/UX Strategy: The "Cozy" Aesthetic
----------------------------------------

**Visual Language:**

*   **Background:** Slow, drifting gradients (Rose/Teal/Slate).
    
*   **Containers:** Translucent white (bg-white/40) with backdrop-blur-xl.
    
*   **Typography:** Rounded sans-serif (Quicksand).
    

**Interaction Model:**

1.  **Upload:** Drag & Drop.
    
2.  **Scanning:** "Breathing" text animations.
    
3.  **The Editor:** A sophisticated two-column layout.
    
    *   _Scrolling:_ Synchronized scrolling (locking the two columns together).
        
    *   _Hover:_ Hovering original highlights the translated counterpart.