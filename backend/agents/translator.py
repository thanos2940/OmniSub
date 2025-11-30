"""
AI Translation Agent using Google Gemini

This module provides context-aware translation capabilities for subtitle files using
Google's Gemini AI model. It supports:
    - Context caching for improved performance
    - Glossary-based term consistency
    - Batch translation processing
    - Gender-aware and case-sensitive translations

Key Features:
    - Context Caching: Reuses glossary/instructions across batches (60-minute TTL)
    - Structured Output: JSON-formatted translations for reliable parsing
    - Fallback Handling: Graceful degradation if caching fails
    - Async Execution: Non-blocking operations using asyncio
"""

import os
import json
import asyncio
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta

import google.generativeai as genai
from google.generativeai import caching

# Initialize Gemini API with environment variable
if "GOOGLE_API_KEY" in os.environ:
    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
else:
    print("[OMBI-LOG] WARNING: GOOGLE_API_KEY not found in environment variables. Gemini agents will fail.")


class TranslatorAgent:
    """
    AI-powered subtitle translator with context caching and glossary support.
    
    This agent uses Google's Gemini model to translate subtitle text while maintaining
    consistency with project-specific terminology, tone, and style guidelines.
    
    Attributes:
        model_name (str): Identifier for the Gemini model to use
        model (GenerativeModel): Configured Gemini model instance
        
    Example:
        >>> translator = TranslatorAgent("gemini-flash-latest")
        >>> cache_name = await translator.create_context_cache(glossary, "Spanish")
        >>> translations, metadata = await translator.translate_batch(
        ...     ["Hello", "Goodbye"], glossary, "Spanish", cache_name
        ... )
    """
    
    def __init__(self, model_name: str = "gemini-flash-latest"):
        """
        Initialize the translator agent with a specific Gemini model.
        
        Args:
            model_name: Gemini model identifier (default: "gemini-flash-latest")
        """
        self.model_name = model_name
        self.model = genai.GenerativeModel(model_name)

    async def create_context_cache(
        self, 
        glossary: Dict, 
        target_language: str
    ) -> Optional[str]:
        """
        Create a reusable context cache for the glossary and translation instructions.
        
        Context caching allows the model to reuse the same glossary and instructions
        across multiple translation batches, improving performance and reducing costs.
        The cache has a 60-minute time-to-live (TTL).
        
        Args:
            glossary: Dictionary containing terms, context_guide, and translation rules
            target_language: Target language for translation (e.g., "Spanish", "Greek")
            
        Returns:
            Cache resource name (string) if successful, None if caching failed
            
        Note:
            - Cache is automatically deleted after 60 minutes
            - If caching fails, translation falls back to standard (non-cached) mode
        """
        glossary_str = json.dumps(glossary, indent=2)
        context_guide = glossary.get("context_guide", "Standard subtitle translation.")
        
        # Build comprehensive system instruction for the AI
        system_instruction = f"""
        You are a professional subtitle translator.
        Target Language: {target_language}
        
        Context Guide (Tone & Style):
        {context_guide}
        
        Context & Glossary:
        {glossary_str}
        
        Instructions:
        1. Translate the subtitle lines provided in the user prompt to {target_language}.
        2. STRICTLY adhere to the glossary terms:
           - If "keep_original" is true, DO NOT translate that term.
           - If "translation" is provided, use it exactly as specified.
           - Respect "case_sensitive" flag (if false, adapt to grammatical context).
           - Respect "gender" field for languages with gendered grammar.
        3. Maintain the tone and style described in the Context Guide.
        4. Return translations in JSON format: {{"translations": [{{"index": 0, "translation": "..."}}]}}
        5. Preserve line breaks within multi-line subtitles.
        6. Use the correct grammatical gender/case based on the glossary and context.
        """
        
        try:
            # Create cache using asyncio.to_thread to avoid blocking
            cache = await asyncio.to_thread(
                caching.CachedContent.create,
                model=self.model_name,
                display_name="ombisub_context_cache",
                system_instruction=system_instruction,
                ttl=timedelta(minutes=60),
            )
            print(f"[OMBI-LOG] TranslatorAgent: Created context cache: {cache.name}")
            return cache.name
            
        except Exception as e:
            print(f"[OMBI-LOG] TranslatorAgent: Failed to create cache: {e}")
            return None

    async def translate_batch(
        self, 
        text_lines: List[str], 
        glossary: Dict, 
        target_language: str = "Greek", 
        cache_name: Optional[str] = None
    ) -> Tuple[List[str], Dict]:
        """
        Translate a batch of subtitle lines with glossary consistency.
        
        This method translates multiple subtitle lines in a single API call, using
        context caching if available. Each line is numbered for reliable order preservation.
        
        Args:
            text_lines: List of original subtitle text (one per entry)
            glossary: Dictionary with terms, translations, and context guide
            target_language: Target language for translation (default: "Spanish")
            cache_name: Optional cache resource name from create_context_cache()
            
        Returns:
            Tuple of:
                - List of translated strings (same length and order as input)
                - Metadata dict with prompt, response, or error information
                
        Example:
            >>> lines = ["Hello world", "How are you?"]
            >>> translations, meta = await translator.translate_batch(
            ...     lines, glossary, "Greek", cache_name
            ... )
            >>> print(translations[0])
            'Γεια σου κόσμε'
            
        Note:
            - Falls back to non-cached mode if cache_name is invalid
            - Pads output with original text if response is incomplete
            - Returns original text unchanged if translation completely fails
        """
        # Format input as numbered lines for reliable indexing
        numbered_lines = [f"{i}: {line}" for i, line in enumerate(text_lines)]
        text_block = "\n".join(numbered_lines)
        
        # Define expected JSON output structure
        output_format = {
            "translations": [
                {"index": 0, "translation": "translated text here"}
            ]
        }
        
        # Try cached execution if cache is available
        if cache_name:
            print(f"[OMBI-LOG] TranslatorAgent: Using Context Cache: {cache_name}")
            
            prompt = f"""
            Translate the following batch:
            
            {text_block}
            
            Output Format:
            {json.dumps(output_format, indent=2)}
            """
            
            try:
                # Load model with cached context
                cached_model = genai.GenerativeModel.from_cached_content(cached_content=cache_name)
                response = await asyncio.to_thread(
                    cached_model.generate_content,
                    prompt, 
                    generation_config={"response_mime_type": "application/json"}
                )
                
            except Exception as e:
                print(f"[OMBI-LOG] TranslatorAgent: Cache error ({e}). Falling back to standard prompt.")
                cache_name = None  # Trigger fallback
        
        # Standard (non-cached) execution
        if not cache_name:
            glossary_str = json.dumps(glossary, indent=2)
            context_guide = glossary.get("context_guide", "Standard subtitle translation.")
            
            prompt = f"""
            You are a professional subtitle translator.
            
            Target Language: {target_language}
            
            Context Guide (Tone & Style):
            {context_guide}
            
            Context & Glossary:
            {glossary_str}
            
            Instructions:
            1. Translate the following subtitle lines to {target_language}.
            2. STRICTLY adhere to the glossary terms:
               - If "keep_original" is true for a term, DO NOT translate it.
               - If a "translation" is provided, use it as the primary translation.
               - If "case_sensitive" is true, enforce EXACT capitalization.
               - If "case_sensitive" is false, adapt capitalization to grammatical context.
               - If a "gender" is specified, use it for grammatical agreement.
            3. Maintain the tone and style described in the Context Guide.
            4. Return the translations in JSON format as an array of objects.
            5. Preserve any line breaks within each subtitle entry.
            
            Input Text (numbered):
            {text_block}
            
            Output Format:
            {json.dumps(output_format, indent=2)}
            """
            
            print(f"[OMBI-LOG] TranslatorAgent: Translating {len(text_lines)} lines to {target_language} using {self.model_name} (No Cache)")
            
            try:
                response = await asyncio.to_thread(
                    self.model.generate_content,
                    prompt, 
                    generation_config={"response_mime_type": "application/json"}
                )
            except Exception as e:
                # Complete failure: return original text unchanged
                print(f"[OMBI-LOG] TranslatorAgent: API call failed: {e}")
                return text_lines, {"prompt": prompt, "error": str(e)}
        
        # Parse and validate response
        print(f"[OMBI-LOG] TranslatorAgent: Response received. Length: {len(response.text)}")
        
        try:
            # Parse JSON response
            result = json.loads(response.text.strip())
            translations = result.get("translations", [])
            
            # Sort by index to ensure correct order
            translations.sort(key=lambda x: x.get("index", 0))
            translated_lines = [t.get("translation", "") for t in translations]
            
            print(f"[OMBI-LOG] TranslatorAgent: Parsed {len(translated_lines)} translations from JSON.")
            
            # Validate response length matches input
            if len(translated_lines) != len(text_lines):
                print(f"[OMBI-LOG] WARNING: Translation count mismatch. Expected {len(text_lines)}, got {len(translated_lines)}")
                
                # Pad with original text if response is too short
                while len(translated_lines) < len(text_lines):
                    translated_lines.append(text_lines[len(translated_lines)])
            
            # Return successful translations
            prompt_summary = "Using Cached Context" if cache_name else prompt
            return translated_lines, {"prompt": prompt_summary, "response": response.text}
            
        except Exception as e:
            # Parsing failure: return original text unchanged
            print(f"[OMBI-LOG] TranslatorAgent: Error parsing response: {e}")
            prompt_summary = "Using Cached Context" if cache_name else prompt
            return text_lines, {"prompt": prompt_summary, "error": str(e)}
