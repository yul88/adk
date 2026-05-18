from fastapi import FastAPI, Request, Response, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uuid
import asyncio
from typing import Dict, Tuple, Optional, List, Any
import logging
import pymongo
import datetime
from bson import ObjectId

# ADK Imports
from google.adk.runners import Runner
from google.adk.apps.app import App
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService
from google.genai import types
from google.adk.utils.context_utils import Aclosing

# Agent Imports
from .agents.stash_agent import Stash
from .agents.checkmate_agent import root_agent as checkmate_agent_prototype
from .tools import ChecklistTools

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# --- Services ---
from .mongo_session_service import MongoDBSessionService

# --- Database Setup ---
import os
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

MONGO_URI_DEFAULT = "mongodb://deckardy:NVwl6b90iL3bsWvda_ZCCyG_ffNDU13ynxYD3-u7UgVApIBr@beaa4e11-214f-4878-8332-ffeb9dee419c.asia-east2.firestore.goog:443/firestore-mongo?loadBalanced=true&tls=true&authMechanism=SCRAM-SHA-256&retryWrites=false"
MONGO_URI = os.environ.get("MONGO_URI", MONGO_URI_DEFAULT)

# Initialize Session Service with Mongo URI
session_service = MongoDBSessionService(db_url=MONGO_URI)
artifact_service = InMemoryArtifactService()
credential_service = InMemoryCredentialService()

client = pymongo.MongoClient(MONGO_URI)
db = client["firestore-mongo"]
users_col = db["users"]
checklists_col = db["checklists"]
links_col = db["links"]

# Initialize Checklist Tools
checklist_tools = ChecklistTools(client)

# --- Session Management ---
# Map: session_token -> (user_id, { "stash": (Runner, session_id, Agent), "checkmate": (Runner, session_id, Agent) })
SessionData = Dict[str, Any]
sessions: Dict[str, SessionData] = {}

class ChatRequest(BaseModel):
    message: str

class AuthRequest(BaseModel):
    username: str
    password: str

class ChecklistItemUpdate(BaseModel):
    text: str
    is_checked: bool
    level: Optional[int] = 0

class ChecklistUpdate(BaseModel):
    items: List[ChecklistItemUpdate]

def _hash_password(password: str) -> str:
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()

from google.adk.tools.tool_context import ToolContext
from google.adk.agents.invocation_context import InvocationContext

def get_tool_context(session: Session, agent: Any, session_service: Any) -> ToolContext:
    """Helper to create a ToolContext with the current session."""
    inv_ctx = InvocationContext(
        session=session,
        agent=agent,
        session_service=session_service,
        invocation_id=str(uuid.uuid4())
    )
    return ToolContext(invocation_context=inv_ctx)

async def get_or_create_session(request: Request) -> Tuple[str, Optional[str]]:
    token = request.cookies.get("session_token")
    if not token or token not in sessions:
        # Check if auth endpoints created a token but didn't initialize runners yet
        # Actually auth endpoints currently use the stash agent to sign in, which might be tricky if we want unified auth.
        # We will implement unified auth directly here instead of using agent tools for login.
        token = str(uuid.uuid4())
        sessions[token] = {
            "user_id": None,
            "runners": {}
        }
    return token, sessions[token]["user_id"]

def get_agent_runner(token: str, agent_name: str, user_id: str):
    session_data = sessions[token]
    
    if agent_name in session_data["runners"]:
        return session_data["runners"][agent_name]

    # Instantiate Agent
    if agent_name == "stash":
        agent_instance = Stash()
        # Pre-set user if logged in (Legacy sync, will move to async ToolContext)
        if user_id:
             if hasattr(agent_instance, "current_user"):
                 agent_instance.current_user = user_id
    elif agent_name == "checkmate":
        # Checkmate prototype is a global object, we need to clone usage or just use it.
        # ADK agents are usually stateful if they have instance vars. Checkmate agent seems stateless in its definition
        # BUT we want to inject tools. 
        # Ideally we create a NEW agent instance based on the prototype configuration.
        # Since Checkmate agent.py defines `root_agent = Agent(...)`, we can't easily "instantiate" a new class if it's not a class.
        # We can create a new Agent instance with the same config.
        from google.adk.agents.llm_agent import Agent
        
        # Define the tool wrapper to bind `self` if needed, or just pass the function
        # The tool function is `checklist_tools.save_checklist`.
        
        agent_instance = Agent(
            model=checkmate_agent_prototype.model,
            name="checkmate_instance",
            description=checkmate_agent_prototype.description,
            instruction=checkmate_agent_prototype.instruction + "\nYou have a tool `save_checklist` to save lists. Do NOT output the full checklist in the chat. Just confirm it has been saved/updated and direct the user to the side panel. ALWAYS generate the checklist content in the SAME LANGUAGE as the user's request. Organize complex lists using nested bullet points (up to 3 levels: Main Item -> Sub Item -> Detail) for better readability.",
            tools=[checklist_tools.save_checklist]
        )
    else:
        raise ValueError("Unknown agent")

    # Create App & Runner
    adk_app = App(name=f"{agent_name}_app_{token.replace('-', '_')}", root_agent=agent_instance)
    runner = Runner(
        app=adk_app,
        artifact_service=artifact_service,
        session_service=session_service,
        credential_service=credential_service,
    )
    
    # Create ADK Session
    # Format user_id for ADK (must be string)
    adk_user_id = user_id if user_id else f"anon_{token[:8]}"
    # We use a blocking call in async function, but creating session is async
    # We need to await this.
    # Since we are in a synchronous helper maybe? No, we should call this async.
    return runner, agent_instance

async def ensure_runner(token: str, agent_name: str):
    session_data = sessions[token]
    user_id = session_data["user_id"]
    
    if agent_name not in session_data["runners"]:
        runner, agent = get_agent_runner(token, agent_name, user_id)
        # We need to create the session ID too
        adk_user_id = user_id if user_id else f"anon_{token[:8]}"
        adk_session = await session_service.create_session(app_name=f"{agent_name}_app_{token}", user_id=adk_user_id)
        
        session_data["runners"][agent_name] = (runner, adk_session.id, agent)
        
        # If user is logged in, sync session state
        if user_id:
           adk_session.state["current_user"] = user_id

    return session_data["runners"][agent_name]

# --- API Endpoints ---

@app.post("/api/signup")
async def signup(request: Request, auth_req: AuthRequest, response: Response):
    token, _ = await get_or_create_session(request)
    response.set_cookie(key="session_token", value=token, httponly=True)
    
    if users_col.find_one({"username": auth_req.username}):
        return {"success": False, "message": f"User '{auth_req.username}' already exists."}
    
    hashed_pw = _hash_password(auth_req.password)
    users_col.insert_one({
        "username": auth_req.username, 
        "password": hashed_pw, 
        "created_at": datetime.datetime.utcnow()
    })
    
    # Login
    sessions[token]["user_id"] = auth_req.username
    return {"success": True, "message": "Signed up and logged in."}

@app.post("/api/signin")
async def signin(request: Request, auth_req: AuthRequest, response: Response):
    token, _ = await get_or_create_session(request)
    response.set_cookie(key="session_token", value=token, httponly=True)
    
    user_doc = users_col.find_one({"username": auth_req.username})
    if not user_doc:
        return {"success": False, "message": "User not found."}
        
    if user_doc["password"] != _hash_password(auth_req.password):
        return {"success": False, "message": "Incorrect password."}
        
    sessions[token]["user_id"] = auth_req.username
    return {"success": True, "message": "Signed in successfully."}

@app.post("/api/signout")
async def signout(request: Request):
    token, _ = await get_or_create_session(request)
    if token in sessions:
        sessions[token]["user_id"] = None
        # Clear runners to reset state
        sessions[token]["runners"] = {}
    return {"success": True, "message": "Signed out."}

@app.get("/api/current_user")
async def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if token and token in sessions:
        return {"username": sessions[token].get("user_id")}
    return {"username": None}

@app.post("/api/chat/{agent_name}")
async def chat(request: Request, agent_name: str, chat_req: ChatRequest, response: Response):
    if agent_name not in ["stash", "checkmate"]:
        raise HTTPException(status_code=404, detail="Agent not found")

    token, user_id = await get_or_create_session(request)
    response.set_cookie(key="session_token", value=token, httponly=True)
    
    runner, session_id, agent = await ensure_runner(token, agent_name)
    
    # Propagate user state if changed externally (though we sync on creation)
    adk_user_id = user_id if user_id else f"anon_{token[:8]}"
    
    # Enforce Login
    if not user_id:
         raise HTTPException(status_code=401, detail="Please sign in to use the agent.")

    # Sync "current_user" in ADK session state for tools
    adk_session = await session_service.get_session(
        app_name=f"{agent_name}_app_{token}", 
        user_id=adk_user_id, 
        session_id=session_id
    )
    
    if not adk_session:
         # Should have been created by ensure_runner, but safe to check
         adk_session = await session_service.create_session(
             app_name=f"{agent_name}_app_{token}", 
             user_id=adk_user_id,
             session_id=session_id
         )
         
    if adk_session:
        # Update session state with User. 
        # For Stash, this is critical if we move to ToolContext state reliance.
        if adk_session.state is None:
             adk_session.state = {}
        adk_session.state["current_user"] = user_id
        await session_service.update_session(adk_session)

    # Prepare ToolContext - Autohandled by Runner
    # tool_context = get_tool_context(adk_session, agent, session_service)

    # For legacy Stash agent (if not yet updated), we might still set attributes, but we are moving to ToolContext.
    if agent_name == "stash" and hasattr(agent, "current_user"):
        agent.current_user = user_id

    # Run Agent
    content = types.Content(role='user', parts=[types.Part(text=chat_req.message)])
    adk_user_id = user_id if user_id else f"anon_{token[:8]}"
    
    agent_response_text = ""
    try:
        async with Aclosing(
            runner.run_async(
                user_id=adk_user_id, 
                session_id=session_id, 
                new_message=content
            )
        ) as agen:
            async for event in agen:
                if event.content and event.content.parts:
                    text = "".join(part.text or "" for part in event.content.parts)
                    agent_response_text += text
    except Exception as e:
        logger.error(f"Error executing agent {agent_name}: {e}")
        # traceback
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

    # Sync state back from Agent to Session (Handle Signout)
    updated_user = user_id
    
    # Reload session state from DB to see if agent tools updated it
    refetched_session = await session_service.get_session(
        app_name=f"{agent_name}_app_{token}", 
        user_id=adk_user_id, 
        session_id=session_id
    )

    if refetched_session:
        # Check if session user has changed
        new_user_in_session = refetched_session.state.get("current_user")
        # If we had a user, but now it's None, it means they signed out.
        if user_id and new_user_in_session is None:
             # Agent initiated signout
             sessions[token]["user_id"] = None
             updated_user = None
             
             # Also clear database session to be sure (optional if agent already did it, but safe)
             refetched_session.state["current_user"] = None
             await session_service.update_session(refetched_session)
    
    return {
        "response": agent_response_text,
        "agent": agent_name,
        "user": updated_user
    }

# --- Checklist API ---

def serialize_doc(doc):
    doc["_id"] = str(doc["_id"])
    return doc

@app.get("/api/checklists")
async def get_checklists(request: Request):
    token, user_id = await get_or_create_session(request)
    if not user_id:
        return []
    
    lists = list(checklists_col.find({"username": user_id}).sort("created_at", -1))
    return [serialize_doc(d) for d in lists]

@app.put("/api/checklists/{list_id}")
async def update_checklist(request: Request, list_id: str, update: ChecklistUpdate):
    token, user_id = await get_or_create_session(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    result = checklists_col.update_one(
        {"_id": ObjectId(list_id), "username": user_id},
        {"$set": {"items": [item.dict() for item in update.items], "updated_at": datetime.datetime.utcnow()}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Checklist not found")
        
    return {"success": True}

@app.get("/api/links")
async def get_links(request: Request):
    token, user_id = await get_or_create_session(request)
    if not user_id:
        return []
    
    links = list(links_col.find({"username": user_id}).sort("created_at", -1))
    return [serialize_doc(d) for d in links]

@app.delete("/api/checklists/{list_id}")
async def delete_checklist(request: Request, list_id: str):
    token, user_id = await get_or_create_session(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    result = checklists_col.delete_one({"_id": ObjectId(list_id), "username": user_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Checklist not found")
        
    return {"success": True}

@app.delete("/api/links/{link_id}")
async def delete_link(request: Request, link_id: str):
    token, user_id = await get_or_create_session(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    result = links_col.delete_one({"_id": ObjectId(link_id), "username": user_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Link not found")
        
    return {"success": True}

# --- Static Files ---
app.mount("/", StaticFiles(directory="mySaaS/static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
