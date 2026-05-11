ADK Seminar Analysis & Code Cohesion Guide
==========================================

This guide compacts the 5-day seminar's whitepapers and notebooks into a single, cohesive document. It focuses on the core theory and provides the specific code patterns from the Google ADK (Agent Development Kit) to ensure your projects are clean, on-point, and aligned with the seminar's best practices.

Day 1: Agent Fundamentals & Architectures
-----------------------------------------

Day 1 establishes that an agent is more than just a model. It's a system with a "brain" (Model), "hands" (Tools), and a "nervous system" (Orchestration).

### 1\. The Core of a Single Agent (from Notebook 1a)

An agent's goal is to move beyond a simple prompt -> text loop to a prompt -> thought -> action -> observation -> answer cycle.

**Theory:** The simplest agent consists of an Agent class, which holds the model and tools, and a Runner, which manages the execution and session.

**Code Cohesion:**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- Imports ---  from google.adk.agents import Agent  from google.adk.models.google_llm import Gemini  from google.adk.runners import InMemoryRunner  from google.adk.tools import google_search  from google.genai import types  # --- Configuration ---  # Define retry logic for API calls  retry_config = types.HttpRetryOptions(attempts=5)  # --- 1. Define the Agent ---  # An agent is defined by its model, instructions, and tools.  root_agent = Agent(      name="helpful_assistant",      model=Gemini(          model="gemini-2.5-flash-lite",          retry_options=retry_config      ),      instruction="You are a helpful assistant. Use Google Search for current info.",      tools=[google_search],  )  # --- 2. Define the Runner ---  # The runner manages the agent's execution.  runner = InMemoryRunner(agent=root_agent)  # --- 3. Run the Agent ---  # .run_debug() is the simplest way to run a one-off query.  # 'await' is necessary as ADK is asynchronous.  # response = await runner.run_debug(  #     "What's the weather in London?"  # )   `

### 2\. Multi-Agent Architectures (from Notebook 1b)

Don't build one complex "monolithic" agent. Build a "team of specialists." There are four main patterns for this.

**Pattern 1: LLM as Coordinator (Dynamic)**

The root agent is an LLM that decides which specialist agent to call as a tool.

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- Imports ---  from google.adk.tools import AgentTool  # ... other imports (Agent, Gemini, etc.)  # --- 1. Define Specialist Agents ---  research_agent = Agent(      name="ResearchAgent",      model=Gemini(model="gemini-2.5-flash-lite"),      instruction="Your job is to find information.",      tools=[google_search],      output_key="research_findings", # Saves output to this key in state  )  summarizer_agent = Agent(      name="SummarizerAgent",      model=Gemini(model="gemini-2.5-flash-lite"),      # It reads the output from the previous agent      instruction="Summarize the findings: {research_findings}",      output_key="final_summary",  )  # --- 2. Define Root Coordinator ---  # The root agent's tools are the *other agents*.  root_agent = Agent(      name="ResearchCoordinator",      model=Gemini(model="gemini-2.5-flash-lite"),      instruction="First call ResearchAgent, then call SummarizerAgent.",      tools=[          AgentTool(research_agent),          AgentTool(summarizer_agent)      ],  )   `

**Pattern 2: SequentialAgent (Deterministic Pipeline)**

Forces agents to run in a fixed "assembly line" order.

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- Imports ---  from google.adk.agents import SequentialAgent  # ... other imports  # Define outline_agent, writer_agent, editor_agent...  # Use 'output_key' on each to save results.  # --- Define the Sequential Workflow ---  root_agent = SequentialAgent(      name="BlogPipeline",      # The order of this list is the execution order.      sub_agents=[outline_agent, writer_agent, editor_agent],  )   `

**Pattern 3: ParallelAgent (Concurrent Tasks)**

Runs multiple agents at the same time. Useful for independent tasks, like researching three different topics.

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- Imports ---  from google.adk.agents import ParallelAgent  # ... other imports  # Define tech_researcher, health_researcher, finance_researcher...  # --- Define the Parallel Workflow ---  parallel_research_team = ParallelAgent(      name="ParallelResearchTeam",      sub_agents=[tech_researcher, health_researcher, finance_researcher],  )  # You often combine patterns. A parallel step followed by a sequential step.  root_agent = SequentialAgent(      name="ResearchSystem",      sub_agents=[parallel_research_team, aggregator_agent], # 'aggregator_agent' runs after all parallel tasks finish  )   `

**Pattern 4: LoopAgent (Iterative Refinement)**

Runs a set of agents repeatedly until a condition is met (max\_iterations or a tool call breaks the loop).

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- Imports ---  from google.adk.agents import LoopAgent  from google.adk.tools import FunctionTool  # ... other imports  # This function will be a tool that signals the loop to stop  def exit_loop():      """Call this to exit the refinement loop."""      return {"status": "approved"}  # Define a critic_agent and a refiner_agent...  # The refiner_agent has the 'exit_loop' tool  refiner_agent = Agent(      name="RefinerAgent",      instruction="...If critique is 'APPROVED', call exit_loop...",      tools=[FunctionTool(exit_loop)],  )  # --- Define the Loop Workflow ---  story_refinement_loop = LoopAgent(      name="StoryRefinementLoop",      sub_agents=[critic_agent, refiner_agent], # Runs this sequence repeatedly      max_iterations=3, # Safety stop  )   `

Day 2: Advanced Tools & Patterns
--------------------------------

Day 2 focuses on giving your agents custom capabilities beyond built-in tools like Google Search.

### 1\. Custom Function Tools (from Notebook 2a)

Any Python function can be a tool. The key is to use **clear docstrings** and **type hints**, as the LLM uses these to generate the function call.

**Theory:** A tool should be well-documented and return a structured dictionary ({"status": "success", ...}) so the agent can handle errors.

**Code Cohesion:**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- Imports ---  from google.adk.tools import FunctionTool  # ... other imports  # --- 1. Define the Custom Function ---  # Use clear type hints and a descriptive docstring.  def get_exchange_rate(base_currency: str, target_currency: str) -> dict:      """      Looks up and returns the exchange rate between two currencies.      Args:          base_currency: The ISO 4217 code of the currency to convert from (e.g., "USD").          target_currency: The ISO 4217 code of the currency to convert to (e.g., "EUR").      Returns:          A dictionary with status and rate, or an error.          Success: {"status": "success", "rate": 0.93}          Error: {"status": "error", "error_message": "Unsupported pair"}      """      # Mock database for the demo      rate_database = {"usd": {"eur": 0.93, "jpy": 157.50}}      rate = rate_database.get(base_currency.lower(), {}).get(target_currency.lower())      if rate:          return {"status": "success", "rate": rate}      else:          return {"status": "error", "error_message": "Unsupported currency pair"}  # --- 2. Create the Agent ---  currency_agent = Agent(      name="currency_agent",      model=Gemini(model="gemini-2.5-flash-lite"),      instruction="You are a currency assistant. Use your tools to find rates and fees.",      tools=[          # Add the Python function directly to the list          get_exchange_rate,           # You can also wrap it in FunctionTool, but this is cleaner          # FunctionTool(get_exchange_rate)       ],  )   `

### 2\. Long-Running Operations (LROs) (from Notebook 2b)

This is the "Human-in-the-Loop" pattern. The agent must pause, wait for external input (like a human clicking "Approve"), and then resume.

**Theory:** The tool function must:

1.  Receive the ToolContext object as a parameter.
    
2.  Check if approval has _already_ been given (tool\_context.tool\_confirmation).
    
3.  If not, _request_ approval using tool\_context.request\_confirmation(...) and return a "pending" status.
    
4.  The App itself must be configured with ResumabilityConfig(is\_resumable=True).
    

**Code Cohesion:**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- Imports ---  from google.adk.tools.tool_context import ToolContext  from google.adk.apps.app import App, ResumabilityConfig  # ... other imports  # --- 1. Define the Pausable Tool ---  def place_shipping_order(num_containers: int, destination: str, tool_context: ToolContext) -> dict:      """Places a shipping order. Requires approval if > 5 containers."""      LARGE_ORDER_THRESHOLD = 5      # Case 1: Small order, auto-approve      if num_containers <= LARGE_ORDER_THRESHOLD:          return {"status": "approved", "order_id": "ORD-123"}      # Case 3: Resuming *after* human approval      if tool_context.tool_confirmation:          if tool_context.tool_confirmation.confirmed:              return {"status": "approved", "order_id": "ORD-456"}          else:              return {"status": "rejected", "message": "Order was rejected by user"}      # Case 2: Large order, first time. Request approval and pause.      tool_context.request_confirmation(          hint=f"Approve order for {num_containers} containers to {destination}?",          payload={"num_containers": num_containers}      )      return {"status": "pending", "message": "Order requires human approval"}  # --- 2. Define the Agent ---  shipping_agent = Agent(      name="shipping_agent",      model=Gemini(model="gemini-2.5-flash-lite"),      instruction="Place shipping orders. Inform user if approval is needed.",      tools=[place_shipping_order],  )  # --- 3. Define the Resumable App ---  # This is required for LROs to work  shipping_app = App(      name="shipping_coordinator",      root_agent=shipping_agent,      resumability_config=ResumabilityConfig(is_resumable=True),  )  # --- 4. The Runner uses the App, not the Agent ---  # runner = Runner(app=shipping_app, session_service=...)   `

Day 3: Context Engineering (Sessions & Memory)
----------------------------------------------

This is the core of agent state. The whitepaper differentiates **Sessions (short-term)** from **Memory (long-term)**.

### 1\. Sessions (Short-Term State) (from Notebook 3a)

A Session is a single conversation. It holds the events (chat history) and state (a working "scratchpad").

**Theory:** InMemorySessionService is for demos (lost on restart). DatabaseSessionService is for production (persists to a DB like SQLite). The session.state is a dictionary agents and tools can read from and write to via the ToolContext.

**Code Cohesion:**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- Imports ---  from google.adk.sessions import DatabaseSessionService, InMemorySessionService  from google.adk.tools.tool_context import ToolContext  # ... other imports  # --- 1. Define a Persistent Session Service ---  # This saves session history to a local SQLite file.  db_url = "sqlite:///my_agent_data.db"  session_service = DatabaseSessionService(db_url=db_url)  # --- 2. Define a Tool that uses Session State ---  # This tool writes to the 'state' scratchpad.  def save_user_name(tool_context: ToolContext, user_name: str) -> dict:      """Saves the user's name to the session state."""      try:          tool_context.state["user_name"] = user_name          return {"status": "success"}      except Exception as e:          return {"status": "error", "error_message": str(e)}  # --- 3. Define an Agent that uses the Tool ---  stateful_agent = Agent(      name="stateful_agent",      model=Gemini(model="gemini-2.5-flash-lite"),      instruction="You are a helpful assistant. Save the user's name if they tell you.",      tools=[save_user_name],  )  # --- 4. The Runner connects the Agent and Session Service ---  runner = Runner(      agent=stateful_agent,      app_name="MyApp",      session_service=session_service  )   `

### 2\. Memory (Long-Term Knowledge) (from Notebook 3b)

Memory persists information _across_ sessions. A "glossary" is a perfect example. This is a two-step process: **Ingest** (saving to memory) and **Retrieve** (reading from memory).

**Code Cohesion:**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- Imports ---  from google.adk.memory import InMemoryMemoryService  from google.adk.tools import load_memory, preload_memory  from google.adk.agents import LlmAgent # Using LlmAgent here as in notebook  from google.adk.agents.callback_context import CallbackContext  # ... other imports  # --- 1. Define the Memory Service ---  # (Using InMemory for demo; VertexAiMemoryBankService is for production)  memory_service = InMemoryMemoryService()  # --- 2. Ingesting into Memory (Manual) ---  # You must first get the session, then add it to memory.  # This is often done at the end of a conversation.  # session = await session_service.get_session(...)  # await memory_service.add_session_to_memory(session)  # --- 3. Retrieving from Memory (Agent Tools) ---  # Give the agent tools to access its long-term memory.  retrieval_agent = LlmAgent(      name="MemoryAgent",      model=Gemini(model="gemini-2.5-flash-lite"),      instruction="Answer user questions. Use your memory tools if you need to recall past info.",      tools=[          # REACTIVE: Agent *decides* to call this tool to search memory.          load_memory,          # PROACTIVE: This tool *automatically runs* before every turn,          # stuffing relevant memories into the agent's context.          # preload_memory,       ],  )  # --- 4. Automating Ingest (Callbacks) ---  # This function will run *after* every agent turn.  async def auto_save_to_memory(callback_context: CallbackContext):      """Callback to automatically save the session to memory."""      await callback_context._invocation_context.memory_service.add_session_to_memory(          callback_context._invocation_context.session      )  # Create an agent that auto-saves  auto_memory_agent = LlmAgent(      name="AutoMemoryAgent",      model=Gemini(model="gemini-2.5-flash-lite"),      instruction="I automatically remember everything.",      tools=[preload_memory], # Auto-retrieves      after_agent_callback=auto_save_to_memory, # Auto-saves  )  # --- 5. The Runner needs BOTH services ---  runner = Runner(      agent=auto_memory_agent,      app_name="MyApp",      session_service=session_service,      memory_service=memory_service, # Add the memory service here  )   `

Day 4: Agent Quality
--------------------

How to trust your agent? You must **Observe** it (debug) and **Evaluate** it (test).

### 1\. Observability (Seeing the "Why") (from Notebook 4a)

You can't fix what you can't see. The three pillars are **Logs**, **Traces**, and **Metrics**.

**Theory:**

*   **Logs:** A record of a single event (e.g., "Tool called").
    
*   **Traces:** Connects logs into a story (e.g., "User query -> Agent thought -> Tool call -> Tool error -> Agent final answer").
    
*   **Metrics:** Aggregated data (e.g., "Tool error rate is 15%").
    

**Code Cohesion:**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- 1. Debugging Locally (CLI) ---  # This is a shell command, not Python.  # It starts the web UI in debug mode, showing all traces.  # !adk web --log_level DEBUG  # --- 2. Production Logging (Plugin) ---  # For a deployed agent, use the LoggingPlugin to automatically  # capture logs and traces.  from google.adk.plugins.logging_plugin import LoggingPlugin  from google.adk.runners import InMemoryRunner # Or other runners  # Add the plugin to your runner  runner = InMemoryRunner(      agent=root_agent,      plugins=[          LoggingPlugin()      ],  )   `

### 2\. Evaluation (Testing for Quality) (from Notebook 4b)

How do you test a non-deterministic agent? You evaluate its **Output** (the final answer) and its **Trajectory** (the steps it took).

**Theory:** You create a "golden set" of test cases (.evalset.json) that define the user's prompt, the _expected_ final answer, and the _expected_ tool calls. The adk eval command runs your agent against this set and reports any differences.

**Code Cohesion:**

This is about configuration files, not just Python code.

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   /* This is a snippet of an 'my_tests.evalset.json' file.  It defines one test case.  */  {    "eval_set_id": "my_agent_tests",    "eval_cases": [      {        "eval_id": "test_basic_weather",        "conversation": [          {            "user_content": {              "parts": [{"text": "What is the weather in London?"}]            },            // 1. This is the expected FINAL OUTPUT            "final_response": {              "parts": [{"text": "The weather in London is sunny and 22°C."}]            },            // 2. This is the expected TRAJECTORY            "intermediate_data": {              "tool_uses": [                {                  "name": "get_weather",                  "args": {                    "location": "London, UK"                  }                }              ]            }          }        ]      }    ]  }   `

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # You run the evaluation from the command line:  !adk eval \    path/to/your/agent_directory \    path/to/your/my_tests.evalset.json \    --print_detailed_results   `

Day 5: Production & Interoperability
------------------------------------

Getting your agent out of the notebook and into the world.

### 1\. Agent2Agent (A2A) Protocol (from Notebook 5a)

Use A2A when agents are **external** (different teams, different companies, or different languages). This is different from AgentTool, which is for _internal_ agents in the same codebase.

**Theory:**

1.  **Expose:** You wrap your agent in a server using to\_a2a(). This creates an "Agent Card" (a JSON file describing the agent).
    
2.  **Consume:** Your main agent uses RemoteA2aAgent to connect to the external agent's URL, treating it just like another sub-agent.
    

**Code Cohesion:**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # --- Imports ---  from google.adk.a2a.utils.agent_to_a2a import to_a2a  from google.adk.agents.remote_a2a_agent import (      RemoteA2aAgent,      AGENT_CARD_WELL_KNOWN_PATH,  )  # --- 1. Agent A: The Service (e.g., a Product Catalog) ---  # First, define your specialist agent  product_catalog_agent = Agent(      name="product_catalog_agent",      # ... tools, model, instructions ...  )  # Now, wrap it in an A2A server app  # This code would run in its own process (e.g., on a server)  product_catalog_a2a_app = to_a2a(      product_catalog_agent,      port=8001 # Expose on port 8001  )  # (You would then run this app with a server like uvicorn)  # --- 2. Agent B: The Consumer (e.g., a Support Agent) ---  # Define the remote agent as a "proxy" object  remote_product_agent = RemoteA2aAgent(      name="product_catalog_agent",      description="Remote agent for product info.",      # It finds the agent by its "Agent Card" URL      agent_card=f"http://localhost:8001{AGENT_CARD_WELL_KNOWN_PATH}",  )  # Your main agent uses the remote agent as a sub-agent  customer_support_agent = LlmAgent(      name="customer_support_agent",      model=Gemini(model="gemini-2.5-flash-lite"),      instruction="You are a support agent. Use the product_catalog_agent to get info.",      # Note: 'sub_agents' is used here, not 'tools'      sub_agents=[remote_product_agent],  )   `

### 2\. Deployment (from Notebook 5b)

The "Prototype to Production" lifecycle. You deploy your agent to a managed, scalable platform like Vertex AI Agent Engine.

**Theory:** The adk deploy command packages your agent's directory. This directory _must_ contain specific files to be deployable.

**Code Cohesion:**

A deployable agent requires a specific file structure.

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   my_deployable_agent/  ├── agent.py                  # Your main agent logic (defines root_agent)  ├── requirements.txt          # All Python dependencies (e.g., google-adk)  ├── .env                      # Environment variables (e.g., GOOGLE_CLOUD_LOCATION)  └── .agent_engine_config.json # Hardware specs (CPU, memory)   `

**agent.py:**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # Must define a variable named 'root_agent'  from google.adk.agents import Agent  from google.adk.models.google_llm import Gemini  # ...  root_agent = Agent(...)   `

**requirements.txt:**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   google-adk  # ... any other libraries you use, e.g., pandas   `

**.agent\_engine\_config.json:**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   {    "min_instances": 0,    "max_instances": 1,    "resource_limits": {      "cpu": "1",      "memory": "1Gi"    }  }   `

**Deployment Command (CLI):**

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # This command packages and deploys your agent directory  !adk deploy agent_engine \    --project=$YOUR_PROJECT_ID \    --region=$YOUR_REGION \    my_deployable_agent \    --agent_engine_config_file=my_deployable_agent/.agent_engine_config.json   `



### Features to Include in Your Project Submission

In your submission, you must demonstrate what you’ve learned in this course by applying at least three (3) of the key concepts listed below:

*   Multi-agent system, including any combination of:
    
    *   Agent powered by an LLM
        
    *   Parallel agents
        
    *   Sequential agents
        
    *   Loop agents
        
*   Tools, including:
    
    *   MCP
        
    *   custom tools
        
    *   built-in tools, such as Google Search or Code Execution
        
    *   OpenAPI tools
        
    *   Long-running operations (pause/resume agents)
        
*   Sessions & Memory
    
    *   Sessions & state management (e.g. InMemorySessionService)
        
    *   Long term memory (e.g. Memory Bank)
        
*   Context engineering (e.g. context compaction)
    
*   Observability: Logging, Tracing, Metrics
    
*   Agent evaluation
    
*   A2A Protocol
    
*   Agent deployment