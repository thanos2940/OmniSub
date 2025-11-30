
import asyncio
import os
from uuid import uuid4
from dotenv import load_dotenv
from pathlib import Path

# Load env vars
load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner, types as adk_types, InMemorySessionService

async def main():
    print("Initializing...")
    model = Gemini(model="gemini-flash-latest")
    agent = Agent(name="DebugAgent", model=model, instruction="Say 'Hello World' and nothing else.")
    
    session_service = InMemorySessionService()
    session_id = f"debug_{uuid4().hex[:8]}"
    
    print(f"Creating session {session_id}...")
    await session_service.create_session(
        session_id=session_id,
        user_id="default_user",
        app_name=f"OmbiSub_{session_id}"
    )
    
    runner = Runner(agent=agent, app_name=f"OmbiSub_{session_id}", session_service=session_service)
    
    print("Running agent...")
    async for event in runner.run_async(
        user_id="default_user",
        session_id=session_id,
        new_message=adk_types.Content(role="user", parts=[adk_types.Part(text="Hi")])
    ):
        print("\n--- Event Received ---")
        try:
            # Try Pydantic dump
            if hasattr(event, 'model_dump'):
                print(f"Model Dump: {event.model_dump()}")
            elif hasattr(event, 'dict'):
                print(f"Dict: {event.dict()}")
        except Exception as e:
            print(f"Could not dump model: {e}")

        # Check specific fields
        fields = ['output_transcription', 'output', 'text', 'parts', 'content']
        for field in fields:
            if hasattr(event, field):
                print(f"{field}: {getattr(event, field)}")
            
        # Print dir
        print(f"Dir: {[d for d in dir(event) if not d.startswith('_')]}")

if __name__ == "__main__":
    asyncio.run(main())
