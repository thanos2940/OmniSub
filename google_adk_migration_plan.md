OmbiSub → Google ADK Migration Plan
===================================

**Version:** 1.0 | **Date:** 2025-11-30

Executive Summary
-----------------

Complete transition plan from manual Gemini API to Google ADK framework.

**Goals:**

*   ✅ Preserve 100% functionality (no user changes)
    
*   ✅ Meet seminar requirements (5+ ADK concepts)
    
*   ✅ Improve architecture (cleaner, production-ready)
    
*   ✅ Enable deployment (Vertex AI ready)
    

**Timeline:** 2-3 days | **Risk:** Low

Current → Target Architecture
-----------------------------

### Current (Manual)

`   Upload SRT → FastAPI → CartographerAgent.generate_glossary()  → Manual Gemini call → Manual JSON save → TranslatorAgent.translate()  → Manual Gemini call → Manual JSON save   `

### Target (ADK)

`   Upload SRT → FastAPI → ADK Runner(CartographerAgent)  → ADK manages (retries, state, errors) → Auto-save to Session  → ADK Runner(SequentialAgent[Cartographer→Translator])  → ADK manages (caching, orchestration) → Auto-save to Session   `

Phase 1: Foundation
-------------------

### Step 1.1: Install Dependencies

**File:** 

backend/requirements.txt

`   fastapiuvicornpython-multipart-google-generativeai+google-adk+google-cloud-aiplatformpython-dotenv   `

`   cd backend && pip install google-adk google-cloud-aiplatform   `

### Step 1.2: Create Project Structure

`   backend/├── adk_agents/              # NEW│   ├── cartographer_agent.py│   ├── translator_agent.py│   └── tools/│       ├── glossary_tools.py│       └── srt_tools.py├── adk_config/              # NEW│   ├── session_service.py│   └── memory_service.py├── agents/                  # KEEP (legacy reference)   `

### Step 1.3: Session Service

**New:** backend/adk\_config/session\_service.py

`   from google.adk.sessions import DatabaseSessionServicefrom pathlib import PathSESSION_DB = Path(__file__).parent.parent / "ombisub_sessions.db"def get_session_service():    return DatabaseSessionService(db_url=f"sqlite:///{SESSION_DB}")   `

**What it replaces:**

*   Manual JSON read/write for project metadata
    
*   storage.load\_project\_metadata() / storage.save\_project\_metadata()
    

**Benefits:**

*   Automatic transaction safety
    
*   Built-in versioning
    
*   Concurrent access handling
    

### Step 1.4: Memory Service

**New:** backend/adk\_config/memory\_service.py

`   from google.adk.memory import InMemoryMemoryServicedef get_memory_service():    return InMemoryMemoryService()    # Production: VertexAiMemoryBankService(project_id="...", location="...")   `

**What it enables:**

*   Cross-project glossary sharing
    
*   "Frieren S01" glossary auto-suggests for "Frieren S02"
    
*   RAG-based term retrieval
    

Phase 2: Agent Conversion
-------------------------

### Step 2.1: SRT Tools

**New:** backend/adk\_agents/tools/srt\_tools.py

`   from google.adk.tools import FunctionToolfrom utils.srt_parser import parse_srtdef parse_srt_file(content: str) -> dict:    """Parse SRT into structured data."""    try:        data = parse_srt(content)        return {"status": "success", "data": data} if data else \               {"status": "error", "error_message": "Invalid SRT"}    except Exception as e:        return {"status": "error", "error_message": str(e)}parse_srt_tool = FunctionTool(parse_srt_file)   `

**Change:** Function → ADK Tool (enables LLM function calling)

### Step 2.2: Cartographer Agent

**New:** backend/adk\_agents/cartographer\_agent.py

`   from google.adk.agents import Agentfrom google.adk.models.google_llm import Geminifrom google.adk.tools import google_searchdef create_cartographer_agent(model_name="gemini-2.0-flash-exp"):    return Agent(        name="CartographerAgent",        model=Gemini(model=model_name),        instruction="""Extract glossary terms from subtitles.Output JSON: {"terms": [{"term": "X", "translation": "Y", ...}]}""",        tools=[google_search],        output_key="glossary_result"    )   `

**Before vs After:**

AspectBeforeAfterDefinitionPython classADK AgentModelgenai.Client()Gemini()ErrorsManual try/catchAuto-retryStateManual JSONoutput\_key

### Step 2.3: Translator Agent

**New:** backend/adk\_agents/translator\_agent.py

`   def create_translator_agent(model_name="gemini-2.0-flash-exp", glossary=None, target_language="English"):    glossary_text = "\n".join([f"- {t['term']} → {t['translation']}"                                 for t in glossary.get("terms", [])]) if glossary else ""        return Agent(        name="TranslatorAgent",        model=Gemini(model=model_name),        instruction=f"""Translate to {target_language}.Use these exact translations:{glossary_text}Input: Numbered lines. Output: Same format, translated.""",        output_key="translation_result"    )   `

### Step 2.4: Sequential Pipeline

**New:** backend/adk\_agents/translation\_pipeline.py

`   from google.adk.agents import SequentialAgentdef create_translation_pipeline(project_name, target_language, glossary,                                  skip_glossary=False):    sub_agents = []        if not skip_glossary:        sub_agents.append(create_cartographer_agent())        sub_agents.append(create_translator_agent(        glossary=glossary, target_language=target_language    ))        return SequentialAgent(        name=f"Pipeline_{project_name}",        sub_agents=sub_agents    )   `

**Seminar:** ✅ Sequential Agents

Phase 3: Session Integration
----------------------------

### Step 3.1: Session Manager

**New:** backend/adk\_config/session\_manager.py

`   from google.adk.sessions import Sessionfrom .session_service import get_session_serviceclass OmbiSubSessionManager:    def __init__(self):        self.session_service = get_session_service()        async def get_or_create_project_session(self, project_name, metadata=None):        session_id = f"ombisub_project_{project_name}"        try:            return await self.session_service.get_session(session_id)        except:            session = await self.session_service.create_session(session_id)            if metadata:                session.state.update({                    "glossary": metadata.get("glossary", {"terms": []}),                    "context_guide": metadata.get("context_guide", ""),                    "target_language": metadata.get("target_language", "English"),                })                await self.session_service.save_session(session)            return session        async def update_glossary(self, project_name, glossary):        session = await self.get_or_create_project_session(project_name)        session.state["glossary"] = glossary        await self.session_service.save_session(session)   `

Phase 4: FastAPI Integration
----------------------------

### Step 4.1: Runner Factory

**New:** backend/adk\_config/runner\_factory.py

`   from google.adk.runners import Runnerfrom .session_service import get_session_servicefrom .memory_service import get_memory_serviceclass OmbiSubRunnerFactory:    def __init__(self):        self.session_service = get_session_service()        self.memory_service = get_memory_service()        def create_runner(self, agent, session_id):        return Runner(            agent=agent,            app_name="OmbiSub",            session_service=self.session_service,            memory_service=self.memory_service        )   `

### Step 4.2: Update Endpoints

**Modified:** 

backend/main.py

Add initialization:

`   from adk_agents.cartographer_agent import create_cartographer_agentfrom adk_config.runner_factory import OmbiSubRunnerFactoryfrom adk_config.session_manager import OmbiSubSessionManageradk_runner_factory = OmbiSubRunnerFactory()adk_session_manager = OmbiSubSessionManager()   `

Replace background task:

`   async def _process_enhance_glossary_adk(job_id, project_name, episode_names, model):    update_job(job_id, status="running", message="Starting...")        # Gather text    text_lines = await _gather_project_text(project_name, episode_names=episode_names)        # Get current state    state = await adk_session_manager.get_project_state(project_name)    glossary = state.get("glossary", {"terms": []})        # Create agent & runner    agent = create_cartographer_agent(model_name=model)    runner = adk_runner_factory.create_runner(        agent, f"ombisub_project_{project_name}"    )        # Run agent    prompt = f"Extract glossary:\n{chr(10).join(text_lines[:5000])}"    response = await runner.run_debug(prompt)        # Parse & save    enhanced = parse_glossary_response(response)    await adk_session_manager.update_glossary(project_name, enhanced)        update_job(job_id, status="completed", result=enhanced)   `

**Key change:**

`   -agent = CartographerAgent(model_name=model)-result, debug = await agent.generate_glossary(text_lines, ...)+agent = create_cartographer_agent(model_name=model)+runner = adk_runner_factory.create_runner(agent, session_id)+response = await runner.run_debug(prompt)   `

Phase 5: Testing
----------------

### Verification Checklist

`   ✓ Create project → Session created in ombisub_sessions.db✓ Upload SRT → Parsing unchanged✓ Enhance glossary → Terms match legacy output✓ Translate episode → Results identical✓ Batch translate → All episodes process✓ Cache reuse → Session state persists✓ Error handling → Graceful failures✓ Concurrent requests → No race conditions   `

### Test Command

`   pytest backend/tests/test_adk_migration.py -v   `

Phase 6: Deployment
-------------------

### Structure

`   backend/deployment/├── agent.py├── requirements.txt└── .agent_engine_config.json   `

**agent.py:**

`   from adk_agents.translation_pipeline import create_translation_pipelineimport osroot_agent = create_translation_pipeline(    project_name=os.getenv("PROJECT_NAME", "OmbiSub"),    target_language=os.getenv("TARGET_LANGUAGE", "English"),    glossary={"terms": []})   `

**.agent\_engine\_config.json:**

`   {  "min_instances": 0,  "max_instances": 3,  "resource_limits": {"cpu": "2", "memory": "4Gi"}}   `

**Deploy:**

`   adk deploy agent_engine \  --project=$PROJECT_ID \  --region=us-central1 \  backend/deployment   `

Seminar Requirements Met
------------------------

RequirementImplementationFileMulti-AgentSequential(Cartographer→Translator)translation\_pipeline.pyCustom ToolsSRT parser, glossary validatortools/Built-in ToolsGoogle Searchcartographer\_agent.pySessionsDatabaseSessionServicesession\_manager.pyMemoryCross-project glossarymemory\_service.pyContext EngineeringGlossary as contexttranslator\_agent.pyDeploymentVertex AI configdeployment/

**Total: 7 concepts** ✅

Timeline
--------

DayPhaseHours1 AMFoundation + Setup3h1 PMAgent Conversion3h2 AMSession Integration4h2 PMFastAPI Updates4h3 AMTesting3h3 PMDeployment Prep3h

**Total: 20 hours over 3 days**

Rollback Plan
-------------

**Phase 1-2:** No impact (new code unused)**Phase 3-4:** Switch endpoints to legacy agents**Phase 5:** Revert to JSON storage

Original code in agents/ kept until full verification.

Post-Migration Cleanup
----------------------

After verification:

1.  Delete agents/cartographer.py (legacy)
    
2.  Delete agents/translator.py (legacy)
    
3.  Remove JSON writes from storage.py
    
4.  Update README with ADK architecture
    

Key Benefits
------------

**Before:** 531 lines (cartographer.py) + 282 lines (translator.py) = 813 lines**After:** ~200 lines total for ADK agents = **75% reduction**

**Improvements:**

*   Automatic error handling & retries
    
*   Built-in observability
    
*   Production-ready architecture
    
*   Deployment capability
    
*   Better state management
    
*   Cleaner code structure
    

Next Steps
----------

1.  **Review plan** with stakeholders
    
2.  **Execute Phase 1** (foundation)
    
3.  **Test after each phase** (don't skip ahead)
    
4.  **Document learnings** for seminar presentation
    
5.  **Prepare demo** showing before/after comparison
    

**Ready to begin migration?** Start with Phase 1: pip install google-adk