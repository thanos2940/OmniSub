"""
AI Glossary Builder (Cartographer Agent) using Google Gemini

This module provides intelligent glossary generation for subtitle translation projects.
The CartographerAgent analyzes subtitle text and/or uses web research to:
    - Identify proper nouns (characters, locations, items)
    - Determine character genders
    - Research canonical spellings and background information
    - Generate context guides for translation tone/style
    - Suggest translations for terminology

Key Features:
    - Web Research: Uses Google Search tool for accurate character/lore information
    - Context Analysis: Analyzes subtitle text for terminology extraction
    - Incremental Enhancement: Adds only NEW terms to existing glossaries
    - Capitalization Awareness: Preserves capitalization patterns in translations
"""

import os
import json
import asyncio
from typing import List, Dict, Tuple, Optional

import google.generativeai as genai
from google.generativeai import protos

# Initialize Gemini API with environment variable
if "GOOGLE_API_KEY" in os.environ:
    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
else:
    print("[OMBI-LOG] WARNING: GOOGLE_API_KEY not found in environment variables. Gemini agents will fail.")


class CartographerAgent:
    """
    AI-powered glossary builder with web research capabilities.
    
    This agent creates and enhances glossaries for subtitle translation projects by
    analyzing subtitle text and performing web research to find accurate terminology,
    character information, and context.
    
    Attributes:
        model_name (str): Identifier for the Gemini model to use
        model (GenerativeModel): Configured Gemini model with Google Search tool enabled
        
    Example:
        >>> cartographer = CartographerAgent("gemini-flash-lite-latest")
        >>> glossary, metadata = await cartographer.generate_glossary(
        ...     text_lines, "Frieren", "Greek", existing_glossary
        ... )
    """
    
    def __init__(self, model_name: str = "gemini-flash-lite-latest"):
        """
        Initialize the cartographer agent with Google Search capabilities.
        
        Args:
            model_name: Gemini model identifier (default: "gemini-flash-lite-latest")
        """
        self.model_name = model_name
        
        # Enable Google Search tool for research capabilities
        tools = [protos.Tool(google_search=protos.Tool.GoogleSearch())]
        self.model = genai.GenerativeModel(model_name, tools=tools)

    async def generate_glossary(
        self, 
        text_lines: List[str], 
        show_name: str = "", 
        target_language: str = "English", 
        existing_glossary: Optional[Dict] = None
    ) -> Tuple[Dict, Dict]:
        """
        Generate or enhance a glossary by analyzing text and performing web research.
        
        This method operates in two modes:
        1. Research-Only Mode (when text_lines is empty): Uses web search to find
           official character/terminology information for the show
        2. Analysis Mode (when text_lines provided): Analyzes subtitle text to extract
           NEW terms not in the existing glossary
        
        Args:
            text_lines: List of subtitle text to analyze (empty list for research-only mode)
            show_name: Name of the show/movie for research context
            target_language: Target language for suggested translations (default: "English")
            existing_glossary: Optional existing glossary to avoid duplicating terms
            
        Returns:
            Tuple of:
                - Dictionary containing:
                    - context_guide: Tone/style guidelines for translation
                    - terms: List of glossary term dictionaries
                - Metadata dict with prompt and response information
                
        Example:
            >>> # Research-only mode
            >>> glossary, meta = await cartographer.generate_glossary(
            ...     [], "Frieren: Beyond Journey's End", "Greek"
            ... )
            >>> # Analysis mode
            >>> glossary, meta = await cartographer.generate_glossary(
            ...     subtitle_lines, "Frieren", "Greek", existing_glossary
            ... )
            
        Note:
            - Google Search is used to verify character genders and canonical spellings
            - Only terms NOT in existing_glossary are added (incremental enhancement)
            - Capitalization in translations matches source term capitalization
        """
        # Combine text for analysis (limited to 30k chars for token safety)
        full_text = "\n".join(text_lines)
        
        # Build prompt based on mode (research-only vs. analysis)
        if not text_lines:
            # Research-Only Mode: Use web search to find show information
            prompt = self._build_research_prompt(show_name, target_language)
            print(f"[OMBI-LOG] CartographerAgent: Researching glossary for {show_name} (No text provided)")
            
        else:
            # Analysis Mode: Extract new terms from subtitle text
            prompt = self._build_analysis_prompt(
                show_name, target_language, full_text[:30000], existing_glossary
            )
            print(f"[OMBI-LOG] CartographerAgent: Generating glossary for {show_name} with {len(text_lines)} lines using {self.model_name}")
        
        print(f"[OMBI-LOG] CartographerAgent: Prompt Preview: {prompt[:500]}...")
        
        # Execute AI request
        try:
            response = await asyncio.to_thread(
                self.model.generate_content,
                prompt, 
                generation_config={"response_mime_type": "application/json"}
            )
            print(f"[OMBI-LOG] CartographerAgent: Raw Gemini Response: {response.text[:500]}...")
            
            # Clean and parse JSON response (handle markdown code blocks)
            cleaned_text = response.text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]
            
            parsed_json = json.loads(cleaned_text)
            print(f"[OMBI-LOG] CartographerAgent: Successfully parsed JSON. Terms count: {len(parsed_json.get('terms', []))}")
            
            return parsed_json, {"prompt": prompt, "response": response.text}
            
        except Exception as e:
            print(f"[OMBI-LOG] CartographerAgent: Error generating glossary: {e}")
            if hasattr(e, 'response'):
                print(f"[OMBI-LOG] CartographerAgent: Error Response Feedback: {e.response.prompt_feedback}")
            return {"terms": []}, {"prompt": prompt, "error": str(e)}

    async def research_project(
        self, 
        show_name: str,
        text_lines: List[str] = None,
        target_language: str = "English"
    ) -> Tuple[str, Dict]:
        """
        Research Agent: Gather comprehensive project analysis for context creation.
        
        This is the first step in the A2A (Agent-to-Agent) workflow for creating
        translation context. It performs deep research and analysis to gather:
        - Character names, backgrounds, and relationships
        - Key locations and their significance
        - Important terminology and concepts
        - Overall tone, genre, and cultural context
        - Linguistic patterns and formality levels
        
        The output is raw research text (NOT glossary JSON) that feeds into
        enhance_context_guide() for final instruction generation.
        
        Args:
            show_name: Name of the show/movie to research
            text_lines: Optional subtitle text to analyze (empty for research-only mode)
            target_language: Target Language for context (default: "English")
            
        Returns:
            Tuple of:
                - Research analysis as formatted text
                - Metadata dict with prompt and response information
                
        Example:
            >>> agent = CartographerAgent()
            >>> research, meta = await agent.research_project(
            ...     "Frieren: Beyond Journey's End",
            ...     subtitle_lines,
            ...     "Greek"
            ... )
            >>> # Feed research into prompt engineer
            >>> instructions, _ = await agent.enhance_context_guide(research, "Frieren")
            
        Note:
            - Uses Google Search for accurate character/lore information
            - Does NOT create or return glossary structure
            - Does NOT save any data - purely returns analysis
            - Designed to feed enhance_context_guide() in A2A workflow
        """
        # Combine text if provided
        text_excerpt = ""
        if text_lines:
            text_excerpt = "\n".join(text_lines[:5000])  # Limit for token safety
        
        # Build research prompt
        prompt = f"""
        You are an expert media researcher and cultural analyst.
        
        Task: Conduct comprehensive research on "{show_name}" to support translation context creation.
        
        **IMPORTANT**: Output your findings as a detailed text analysis, NOT as JSON or glossary format.
        
        Instructions:
        1. **RESEARCH**: Use Google Search to find official information about this title, including:
           - Main characters (names, roles, genders, personalities)
           - Key locations and settings
           - Important terminology, items, or concepts
           - Genre, tone, and cultural context
           - Language style and formality patterns
        
        2. **ANALYZE** (if subtitle text provided below): Examine the dialogue to identify:
           - Speaking patterns and relationship dynamics
           - Recurring terms or phrases
           - Formality levels and register
           - Cultural references or idioms
        
        3. **SYNTHESIZE**: Create a comprehensive narrative analysis covering:
           - **Characters**: Who are the main characters? What are their backgrounds, relationships, and speaking styles?
           - **World/Setting**: Where does this take place? What's the cultural/historical context?
           - **Tone & Genre**: What's the overall atmosphere? (e.g., dark fantasy, lighthearted comedy, serious drama)
           - **Language Patterns**: How do characters speak? Formal vs. informal? Any unique linguistic traits?
           - **Key Terminology**: What important terms, names, or concepts appear frequently?
           - **Translation Guidance**: What should a translator know to preserve the essence and context?
        
        **OUTPUT FORMAT**: 
        Write your analysis as clear, structured text with headings. Be comprehensive and specific.
        This research will be used to create detailed translation instructions for "{target_language}".
        
        DO NOT format as JSON. DO NOT create a glossary structure. Just provide thorough research.
        """
        
        if text_excerpt:
            prompt += f"""
        
        **Subtitle Text to Analyze**:
        {text_excerpt}
        """
        
        print(f"[OMBI-LOG] CartographerAgent: Researching project '{show_name}' for context creation")
        
        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            research_text = response.text.strip()
            
            print(f"[OMBI-LOG] CartographerAgent: Research completed ({len(research_text)} chars)")
            return research_text, {"prompt": prompt, "response": response.text}
            
        except Exception as e:
            print(f"[OMBI-LOG] CartographerAgent: Error during project research: {e}")
            fallback = f"Research failed for {show_name}. Target language: {target_language}."
            return fallback, {"prompt": prompt, "error": str(e)}

    async def enhance_context_guide(
        self, 
        current_guide: str, 
        show_name: str
    ) -> Tuple[str, Dict]:
        """
        Generate comprehensive translation instructions from a basic context guide.
        
        This method transforms a simple context guide into detailed system instructions
        for the translator agent, including heuristics for handling:
        - Gender inference from context
        - Grammatical register (formal vs. informal)
        - Pronoun reference tracking
        - Scene-based contextual analysis
        
        Args:
            current_guide: Existing context guide (simple tone/style description)
            show_name: Name of the show/movie for analysis
            
        Returns:
            Tuple of:
                - Enhanced system instruction text
                - Metadata dict with prompt and response information
                
        Example:
            >>> enhanced, meta = await cartographer.enhance_context_guide(
            ...     "Dark fantasy with formal dialogue", "Frieren"
            ... )
            
        Note:
            - Designed to help translators work with "blind" subtitle text (no speaker labels)
            - Provides inference rules for gender, formality, and context
        """
        prompt = f"""
        Role: You are a Senior Localization Director and Prompt Engineer.

        Task: I will provide you with the Title of a Movie/Series/Anime ("{show_name}") and an existing Context Guide. You must generate a comprehensive System Instruction that will be fed into a separate Translation Agent (the "Worker").

        Critical Constraint (The "Blind" Reader Problem):
        The Translation Worker will receive raw SRT text without character names attached. It does not know who is speaking line-by-line. Your generated instructions must provide heuristics (rules of thumb) for the worker to infer context, gender, and references solely from the dialogue content.

        **IMPORTANT**: Do NOT create character-specific identification heuristics (e.g., "The Kouhai uses 'Senpai'", "The Guardian says 'Fuji-nee'"). Instead, provide GENERAL linguistic patterns that apply broadly across any speaker.

        Current Context Guide to Enhance:
        "{current_guide}"
        
        Output Requirements:

        1. Series Analysis: Briefly identify the genre, setting, and key linguistic traits (e.g., "Victorian London = Formal/Archaic", "Cyberpunk = Tech slang").

        2. The System Instruction: Write the actual prompt for the translator. It MUST include the following detailed sections:

        a) Tone & Atmosphere: Define the overall voice (e.g., "Dark/Gritty", "High Fantasy", "Lighthearted Comedy").

        b) Scene-Based Analysis: Instruct the worker to treat the input not as isolated lines, but as a continuous "scene". It must read the surrounding lines (context window) to understand the situation before translating the current line.

        c) Contextual Reference Tracking (Crucial): This is vital for languages with gendered nouns (like Greek/French).
           - Rule: Instruct the worker to trace pronouns (it, them, that) back to the specific noun mentioned in previous lines.
           - Example: If "it" refers to a "butterfly" (female in target lang), the translation must use the female accusative (e.g., "Kill it" -> "Σκότωσέ την").

        d) Grammatical Register Strategy: Clear GENERAL rules on when to use Formal vs. Informal registers based on textual cues.
           - Focus on PATTERNS, not characters: "Military vocabulary (Sir, Commander, orders) → Formal", "Casual slang or swearing → Informal"
           - Provide rules for detecting power dynamics: "Honorifics, titles, deferent language → Use formal"
           - DO NOT create character archetypes like "The Idealist" or "The Kouhai"

        e) Gender Inference Strategy: GENERAL rules for guessing speaker gender when ambiguous.
           - Heuristic examples: 
             * "Look for gendered adjectives in self-reference (I am tired[masculine/feminine])"
             * "Peer descriptions can reveal gender (vocative case, gendered insults/complements)"
             * "Lock gender once inferred and maintain consistency"
           - Focus on LINGUISTIC CUES, not character identities

        f) Vocabulary & Terminology: General guidance on how to handle specialized terms, cultural references, or domain-specific language that appears in the show.

        g) Handling Ambiguity: Instructions on what to do when context is completely missing (e.g., "Prioritize flow and neutral phrasing over guessing", "Default to masculine in ambiguous cases if target language requires gender").

        **CRITICAL REMINDERS**:
        - Provide GENERAL, reusable patterns that work for ANY speaker
        - Avoid identifying specific characters by speech patterns
        - Focus on linguistic features (vocabulary, grammar, tone) rather than character archetypes
        - The glossary contains character names - the translator already has that information

        Output ONLY the enhanced System Instruction text. Do not include "Here is the enhanced guide:" or quotes. Make it comprehensive and actionable for the translation worker.
        """
        
        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            return response.text.strip(), {"prompt": prompt, "response": response.text}
            
        except Exception as e:
            print(f"[OMBI-LOG] CartographerAgent: Error enhancing context guide: {e}")
            return current_guide, {"prompt": prompt, "error": str(e)}

    async def enhance_glossary(
        self, 
        current_glossary: Dict, 
        show_name: str
    ) -> Tuple[Dict, Dict]:
        """
        Refine and validate an existing glossary for consistency and quality.
        
        This method checks the glossary for:
        - Formatting consistency
        - Capitalization correctness (translations match term capitalization)
        - Duplicate or overly specific terms
        - Missing descriptions
        
        Args:
            current_glossary: Existing glossary with terms list
            show_name: Name of the show/movie for context
            
        Returns:
            Tuple of:
                - Enhanced glossary dictionary
                - Metadata dict with prompt and response information
                
        Example:
            >>> enhanced, meta = await cartographer.enhance_glossary(
            ...     current_glossary, "Frieren"
            ... )
            
        Note:
            - Does NOT remove or change existing terms
            - Only improves formatting and consistency
            - Suggests removing overly specific variants (e.g., "B-rank" when "rank" exists)
        """
        prompt = f"""
        You are an expert terminologist.
        Enhance the following Glossary for the show "{show_name}".
        
        Current Glossary (JSON):
        {json.dumps(current_glossary.get('terms', []), indent=2)}
        
        Task:
        1. Do not change or remove existing terms.
        2. Ensure consistency in formatting.
        3. Only check for new terms in the subtitle text.
        4. Add ONLY unique, specific and special terms. Do not add "B-rank" when "A-rank" or "Rank" is already present, for example.
        5. **Capitalization**: Check the "translation" field. If the "term" is lowercase, the "translation" MUST be lowercase. If the "term" is Title Case, the translation should be Title Case. Fix any violations of this rule.
        6. New terms should be in their generic form (e.g. shadow soldier instead of shadow soldier, or wave of enemies instead of large wave of enemies, Crete island instead of just Crete (island is a standard generic term) etc.).
        
        Output Format (JSON):
        {{
            "terms": [
                {{
                    "term": "Term Name",
                    "translation": "...",
                    "type": "...",
                    "gender": "...",
                    "description": "description...",
                    "keep_original": false,
                    "case_sensitive": false
                }}
            ]
        }}
        """
        
        try:
            response = await asyncio.to_thread(
                self.model.generate_content,
                prompt, 
                generation_config={"response_mime_type": "application/json"}
            )
            
            # Clean and parse JSON response
            cleaned_text = response.text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            if cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]
            
            parsed = json.loads(cleaned_text)
            return parsed, {"prompt": prompt, "response": response.text}
            
        except Exception as e:
            print(f"[OMBI-LOG] CartographerAgent: Error enhancing glossary: {e}")
            return current_glossary, {"prompt": prompt, "error": str(e)}

    # ===== Private Helper Methods =====
    
    def _build_research_prompt(self, show_name: str, target_language: str) -> str:
        """Build prompt for research-only mode (no subtitle text provided)."""
        return f"""
        You are an expert linguist and researcher.
        
        Task: Create a comprehensive glossary and context guide for the show/movie "{show_name}" using ONLY your internal knowledge and Google Search.
        
        1. **RESEARCH**: Use the Google Search tool to find the official characters, terminology, locations, and lore of this title. This is CRITICAL.
        2. Identify key proper nouns (names, places) and unique terminology.
        3. Determine the likely gender of characters.
        4. Provide a "context_guide" paragraph describing the tone, style, and specific context details to guide a translator.
        5. For each term found, provide a suggested translation in {target_language} in the "translation" field.
        
        Output Format (JSON):
        {{
            "context_guide": "A paragraph describing the general tone, style, and specific context details...",
            "terms": [
                {{
                    "term": "Term Name",
                    "translation": "Suggested translation in {target_language}",
                    "type": "person|location|item|concept",
                    "gender": "male|female|neutral|n/a",
                    "description": "Detailed description based on research",
                    "keep_original": false,
                    "case_sensitive": false
                }}
            ]
        }}
        """
    
    def _build_analysis_prompt(
        self, 
        show_name: str, 
        target_language: str, 
        text_excerpt: str, 
        existing_glossary: Optional[Dict]
    ) -> str:
        """Build prompt for text analysis mode (extract NEW terms from subtitle text)."""
        existing_terms_note = ""
        if existing_glossary:
            existing_terms_note = f"""
            **IMPORTANT**: Find ONLY NEW terms that are NOT already in the existing glossary below. Do NOT duplicate existing terms. Focus on discovering fresh terminology, characters, locations, or concepts that haven't been catalogued yet.
            
            Existing Glossary (These terms are ALREADY catalogued - do NOT include them again, find NEW ones):
            {json.dumps(existing_glossary.get('terms', []), indent=2)}
            """
        
        return f"""
        You are an expert linguist and researcher. 
        Analyze the following subtitle text from the show/movie "{show_name}".
        
        Task:
        1. Identify all proper nouns (names, places), unique terminology, and fantasy/sci-fi terms.
        2. **RESEARCH**: Use the Google Search tool to find the official gender, background, and correct spelling of characters and terms. This is CRITICAL.
        3. Determine the likely gender of characters if possible from context.
        {existing_terms_note}
        4. Provide a "context_guide" paragraph describing the tone, style, and specific context details to guide the translator.
        5. For each NEW term found, provide a suggested translation in {target_language} in the "translation" field.
        
        CRITICAL INSTRUCTIONS:
        - **New Terms Only**: If an existing glossary is provided, you MUST only add NEW terms not already present. Check carefully against the existing terms list.
        - **Context/Description**: You MUST provide a detailed description for every term based on the text. Explain WHO a person is, WHAT an item does, or WHERE a place is. Do not leave this empty.
        - **Capitalization**: Respect the capitalization of the original term in your translation. If the original term is lowercase (e.g. "dungeon"), the translation MUST be lowercase (e.g. "μπουντρούμι"). If it is Title Case, the translation must be Title Case. Do not auto-capitalize terms that are not proper nouns.
        
        Output Format (JSON):
        {{
            "context_guide": "A paragraph describing the general tone, style, and specific context details to guide the translator (e.g. 'Dark and gritty', 'Formal court language', 'Use 1920s slang').",
            "terms": [
                {{
                    "term": "Term Name",
                    "translation": "Suggested translation in {target_language} (MATCHING CAPITALIZATION)",
                    "type": "person|location|item|concept",
                    "gender": "male|female|neutral|n/a",
                    "description": "Detailed description based on context found in the text",
                    "keep_original": false,
                    "case_sensitive": false
                }}
            ]
        }}
        
        Subtitle Text:
        {text_excerpt}
        """
