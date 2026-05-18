from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uuid
import asyncio
import logging
from typing import Dict, Tuple, Optional

# ADK Imports
from google.adk.runners import Runner
from google.adk.apps.app import App
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService
from google.genai import types
from google.adk.utils.context_utils import Aclosing

# Tool Context Imports for Agent Compatibility
from google.adk.tools.tool_context import ToolContext
from google.adk.agents.invocation_context import InvocationContext

from .agent import Stash

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Global Services
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session

class RobustInMemorySessionService(InMemorySessionService):
    def __init__(self):
        super().__init__()
        self._robust_sessions: Dict[str, Session] = {}

    async def create_session(self, app_name: str, user_id: str) -> Session:
        session = await super().create_session(app_name=app_name, user_id=user_id)
        self._robust_sessions[session.id] = session
        return session

    async def get_session(self, app_name: str, user_id: str, session_id: str) -> Optional[Session]:
        if session_id in self._robust_sessions:
            return self._robust_sessions[session_id]
        return await super().get_session(app_name=app_name, user_id=user_id, session_id=session_id)

# Load environment variables
from dotenv import load_dotenv
from pathlib import Path
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)
import os

from .mongo_session_service import MongoDBSessionService
mongo_uri = os.environ.get("MONGO_URI")
if not mongo_uri:
    logger.warning("MONGO_URI not found in environment, falling back to InMemory (Session Persistence Disable)")
    session_service = RobustInMemorySessionService()
else:
    logger.info(f"Using MongoDBSessionService with URI: {mongo_uri[:20]}...")
    session_service = MongoDBSessionService(db_url=mongo_uri)
artifact_service = InMemoryArtifactService()
credential_service = InMemoryCredentialService()

# Session Manager
# Maps session_token -> (Runner, session_id, Stash_Instance)
SessionData = Tuple[Runner, str, Stash]
sessions: Dict[str, SessionData] = {}

class ChatRequest(BaseModel):
    message: str

class AuthRequest(BaseModel):
    username: str
    password: str

def get_agent_user(agent: Stash, session_id: str) -> Optional[str]:
    # Try internal session store if available
    # The agent populates this via _set_user (which syncs from MongoDBSessionService info)
    if hasattr(agent, "_user_sessions"):
        return agent._user_sessions.get(session_id)
    return None

def get_tool_context(session: Session, agent: Stash, session_service: InMemorySessionService) -> ToolContext:
    """Helper to create a ToolContext with the current session."""
    inv_ctx = InvocationContext(
        session=session,
        agent=agent,
        session_service=session_service,
        invocation_id=str(uuid.uuid4())
    )
    return ToolContext(invocation_context=inv_ctx)

async def get_session_data(request: Request) -> Tuple[str, SessionData]:
    token = request.cookies.get("session_token")
    if not token or token not in sessions:
        token = str(uuid.uuid4())
        
        # Create new agent and runner for this session
        agent = Stash()
        adk_app = App(name="stash_app", root_agent=agent)
        
        runner = Runner(
            app=adk_app,
            artifact_service=artifact_service,
            session_service=session_service,
            credential_service=credential_service,
        )
        
        # Create ADK session
        user_id = "user_" + token[:8]
        adk_session = await session_service.create_session(
            app_name="stash_app", user_id=user_id
        )
        
        sessions[token] = (runner, adk_session.id, agent)
        logger.info(f"Created new session: {token} (ADK ID: {adk_session.id})")
    return token, sessions[token]

@app.post("/api/signup")
async def signup(request: Request, auth_req: AuthRequest, response: Response):
    token, (_, session_id, agent) = await get_session_data(request)
    response.set_cookie(key="session_token", value=token, httponly=True)
    
    # Retrieve session to pass to agent
    user_id = "user_" + token[:8]
    session = await session_service.get_session(
        app_name="stash_app", user_id=user_id, session_id=session_id
    )
    
    if session:
        ctx = get_tool_context(session, agent, session_service)
        # Agent methods are now async and expect ToolContext for persistence
        result = await agent.signup(auth_req.username, auth_req.password, tool_context=ctx)
    else:
        result = "Error: Session not found."

    # Manual sync is NOT needed anymore; agent updates session state directly.
    return {"message": result, "success": "successfully" in result}

@app.post("/api/signin")
async def signin(request: Request, auth_req: AuthRequest, response: Response):
    token, (_, session_id, agent) = await get_session_data(request)
    response.set_cookie(key="session_token", value=token, httponly=True)
    
    user_id = "user_" + token[:8]
    session = await session_service.get_session(
        app_name="stash_app", user_id=user_id, session_id=session_id
    )
    
    if session:
        ctx = get_tool_context(session, agent, session_service)
        result = await agent.signin(auth_req.username, auth_req.password, tool_context=ctx)
    else:
        result = "Error: Session not found."
            
    return {"message": result, "success": "successfully" in result}

@app.post("/api/signout")
async def signout(request: Request):
    token = request.cookies.get("session_token")
    if token and token in sessions:
        _, session_id, agent = sessions[token]
        
        user_id = "user_" + token[:8]
        session = await session_service.get_session(
            app_name="stash_app", user_id=user_id, session_id=session_id
        )
        
        if session:
            ctx = get_tool_context(session, agent, session_service)
            await agent.signout(tool_context=ctx)
            
    return {"message": "Signed out"}

@app.post("/chat")
async def chat(request: Request, chat_req: ChatRequest, response: Response):
    token, (runner, session_id, agent) = await get_session_data(request)
    response.set_cookie(key="session_token", value=token, httponly=True)
    
    try:
        current_user = get_agent_user(agent, session_id)
        logger.info(f"User ({current_user or 'anon'}): {chat_req.message}")
        
        content = types.Content(role='user', parts=[types.Part(text=chat_req.message)])
        user_id = "user_" + token[:8]
        
        agent_response_text = ""
        
        async with Aclosing(
            runner.run_async(
                user_id=user_id, session_id=session_id, new_message=content
            )
        ) as agen:
            async for event in agen:
                if event.content and event.content.parts:
                    text = "".join(part.text or "" for part in event.content.parts)
                    agent_response_text += text

        logger.info(f"Agent: {agent_response_text}")
        
        # Re-fetch user in case it changed
        current_user = get_agent_user(agent, session_id)
        return {
            "response": agent_response_text,
            "user": current_user
        }
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/current_user")
async def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if token and token in sessions:
        _, session_id, agent = sessions[token]
        return {"username": get_agent_user(agent, session_id)}
    return {"username": None}

# Mount static files
app.mount("/", StaticFiles(directory="Stash/static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

