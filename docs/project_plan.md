Capstone Project Plan: Context-Aware Subtitle Translator
========================================================

**Author:** \[Your Name\]**Date:** 11/19/2025**Track:** Concierge Agents**Version:** 4.0 (CAT Tool Edition)

1\. Project Pitch (Kaggle Category 1)
-------------------------------------

### 1.1. Problem Statement

Standard machine translation services are "context-blind." They treat subtitle files as isolated strings, failing to understand the specific **Narrative Universe** of the media. This leads to critical errors: literal translation of in-universe terminology (e.g., "Winterfell" becoming "Winter Mountain"), misgendered characters, and tonal inconsistencies. Professional subtitlers spend hours manually correcting these "hallucinations of context."

### 1.2. Proposed Solution: The "Spherical Context" Engine

This project builds a **Context-Aware AI Translation Platform** that prioritizes "Context First." Before translating a single line of dialogue, the system builds a **Spherical Context**—a deep, structured understanding of the show's lore, characters, and terminology.

The system uses a multi-stage AI pipeline to:

1.  **Ingest & Sanitize:** Strip timecodes to focus purely on narrative content.
    
2.  **Deep Scan:** Use specialized agents to perform Named Entity Recognition (NER) and external research, synthesizing a **Master Glossary**.
    
3.  **Context-Cached Translation:** Leverage Gemini 's **Context Caching** to load this heavy context once and apply it efficiently across the entire file with zero latency.
    

### 1.3. Core Value Proposition

*   **Universe Consistency:** Guarantees that terms like "Lightsaber" or "Kamehameha" remain consistent across the entire file.
    
*   **Fluid User Experience:** A "Cozy," non-intrusive UI that empowers the user as an "Editor-in-Chief" rather than a manual laborer.
    
*   **Enterprise-Grade Optimization:** Uses **Context Caching** to reduce token costs and latency for large files.
    
*   **Safety First:** The AI operates on sanitized text arrays, never risking corruption of the delicate subtitle timecodes.
    

2\. Technical Implementation (Kaggle Category 2)
------------------------------------------------

The system follows a decoupled client-server architecture, with the **Google Agent Development Kit (ADK)** powering the backend intelligence.

### 2.1. The Stack

*   **Frontend (The "Cozy" Layer):** React 18 (Vite) + Tailwind CSS + Framer Motion for fluid state transitions.
    
*   **Backend (The "Brain"):** FastAPI (Python) serving as the host for ADK Agents.
    
*   **Orchestration:** Google Gen AI SDK (Gemini) & ADK SequentialAgent workflows.
    
*   **Persistence:** SQLite (MVP) / Firebase (Production) for storing Project States and Master Glossaries.
    

### 2.2. Agent & Tool Architecture

The backend workflow is orchestrated by a **SequentialAgent** pipeline that manages the "Spherical Context" workflow.

#### Phase 1: The Curator (Ingestion)

*   **Role:** Data Sanitization.
    
*   **Tool:** FunctionTool(parse\_srt) - A robust regex parser that separates timecodes from text.
    
*   **Output:** An array of raw text strings \["Winterfell is ours.", "Open the gate!"\]. This ensures the AI never hallucinates timestamp formats.
    

#### Phase 2: The Cartographer (Deep Scan Agent)

*   **Role:** To map the "Narrative Universe."
    
*   **Model:** gemini-flash-lite-latest (Fast, low latency).
    
*   **Instruction:** "You are an expert linguist and researcher. Your job is to extract proper nouns and terminology from the provided text and cross-reference them with external knowledge."
    
*   **Tools:**
    
    *   Google Search: To find character genders ("Game of Thrones character genders list") and wiki definitions.
        
    *   FunctionTool(ner\_scan): An internal tool using a flash model to extract entities from the raw text array.
        
*   **Output:** The **Master Glossary** (JSON), containing deduplicated terms, genders, and translation rules (e.g., keep\_original: true).
    

#### Phase 3: The Translator (Context-Cached Agent)

*   **Role:** High-fidelity translation.
    
*   **Model:** gemini-3-pro (High capacity, reasoning).
    
*   **Optimization:** **Context Caching.**
    
    *   _Why?_ Uploading the Glossary + System Instructions + Tone Guidelines for every batch of subtitles is expensive and slow.
        
    *   _Solution:_ The backend creates a cache key context\_cache\_id containing the Master Glossary and user instructions.
        
*   **Instruction:** "Translate the following batch of text. You MUST adhere to the cached Glossary definitions. Do not translate terms marked keep\_original."
    
*   **Tools:**
    
    *   FunctionTool(batch\_processor): Splits text into 50-line chunks with a 3-line overlap to maintain conversational flow.
        

3\. Alignment with Capstone Requirements
----------------------------------------

This architecture is designed to hit every high-value scoring criteria.

### Required Concepts (3+):

1.  **\[X\] Context Engineering (Strongest Feature):**
    
    *   The entire "Spherical Context" workflow is an advanced application of context engineering. We are not just prompting; we are building a persistent knowledge graph (Glossary) and injecting it via **Context Caching**.
        
2.  **\[X\] Tools:**
    
    *   **Custom Tools:** Regex parsers (parse\_srt), Glossary Managers (update\_glossary), and Batchers.
        
    *   **Built-in Tools:** Google Search for the "Deep Scan" phase.
        
3.  **\[X\] Sessions & Memory:**
    
    *   **Long-Term Memory:** The **Master Glossary** persists across the project lifecycle.
        
    *   **Session State:** The FastAPI backend maintains the state of the file processing (Uploading -> Scanning -> Translating) using ADK's state management principles.
        
4.  **\[X\] Multi-Agent System:**
    
    *   The architecture strictly separates concerns between the **Cartographer** (Research/NER) and the **Translator** (Generation), orchestrated by a parent SequentialAgent.
        
5.  **\[X\] Observability:**
    
    *   We will implement ADK's LoggingPlugin to trace the decision path: _User Upload -> NER Extraction -> Glossary Generation -> Translation Output_.
        

### Bonus Points:

*   **\[X\] Effective Use of Gemini (5 points):**
    
    *   Utilizes **Gemini Flash** for Context Caching (a cutting-edge feature).
        
    *   Utilizes **Gemini Flash Lite** for high-speed NER scanning.
        
*   **\[X\] Agent Deployment (5 points):**
    
    *   The FastAPI backend will be containerized (Docker) and deployed to **Google Cloud Run**, serving the React frontend.
        
*   **\[X\] Video (10 points):**
    
    *   The video will showcase the "Cozy" UI aesthetic and the "Deep Scan" animation as the agent builds the glossary in real-time.
        

4\. Development Plan
--------------------

### Phase 1: The Core Engine (Backend)

1.  Set up the ADK environment wrapped in FastAPI.
    
2.  Implement the Ingestion regex logic.
    
3.  Build the Cartographer Agent with Google Search to generate the JSON Glossary.
    
4.  **Milestone:** Successfully generate a correct glossary for a sample _Game of Thrones_ clip.
    

### Phase 2: Context & Caching

1.  Implement the **Context Caching** logic using the Gemini SDK.
    
2.  Build the Translator Agent loop (Batching -> Translate -> Re-assemble).
    
3.  Validate that glossary rules (e.g., "Winterfell") are respected in the output.
    

### Phase 3: The "Cozy" Interface

1.  Build the React frontend with the split-view Editor.
    
2.  Connect the frontend to the FastAPI endpoints.
    
3.  Implement the **Manual Edit** loop, allowing users to update the Glossary and trigger a re-translation of specific batches.
    

### Phase 4: Polish & Submit

1.  Add LoggingPlugin for observability.
    
2.  Create the evaluation dataset (.evalset.json) to prove translation accuracy.
    
3.  Deploy to Cloud Run.
    
4.  Record demo video.