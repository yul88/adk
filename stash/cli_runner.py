import asyncio
from google.adk.runners import Runner
from google.adk.apps.app import App
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService
from google.genai import types
from google.adk.utils.context_utils import Aclosing
from .agent import Stash
import sys

async def main():
    print("Initializing Stash agent...")
    agent = Stash()
    app = App(name="stash_cli", root_agent=agent)
    
    session_service = InMemorySessionService()
    runner = Runner(
        app=app,
        artifact_service=InMemoryArtifactService(),
        session_service=session_service,
        credential_service=InMemoryCredentialService(),
    )
    
    session = await session_service.create_session(app_name="stash_cli", user_id="cli_user")
    
    print("\n--- Welcome to Stash CLI ---")
    print("Type 'exit' or 'quit' to close.")
    
    # We need to run sync input in async loop.
    # For simplicity in this script, we can just block on input.
    
    while True:
        user = agent._user_sessions.get(session.id, "user")
        try:
            print(f"[{user}]: ", end="", flush=True)
            user_input = sys.stdin.readline().strip()
            if not user_input:
                continue
        except EOFError:
            break
            
        if user_input.lower() in ["exit", "quit"]:
            print("Goodbye!")
            break
            
        try:
            content = types.Content(role='user', parts=[types.Part(text=user_input)])
            
            response_text = ""
            async with Aclosing(
                runner.run_async(
                    user_id="cli_user", session_id=session.id, new_message=content
                )
            ) as agen:
                async for event in agen:
                    if event.content and event.content.parts:
                        text = "".join(part.text or "" for part in event.content.parts)
                        response_text += text
            
            print(f"[stash]: {response_text}")
            
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
