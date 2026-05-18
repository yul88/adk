import os
import logging
import uuid
import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

# ADK Imports
from google.adk.runners import Runner
from google.adk.apps.app import App
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService
from google.genai import types
from google.adk.utils.context_utils import Aclosing

# Agent Imports
from .agent import root_agent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load Env
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

app = FastAPI()

# Services
# ... (Previous imports)
import hashlib
import pymongo
from .mongo_session_service import MongoDBSessionService

# ...

# Database Setup
MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    raise ValueError("MONGO_URI environment variable is not set")

# Initialize Services
session_service = MongoDBSessionService(db_url=MONGO_URI)
artifact_service = InMemoryArtifactService()
credential_service = InMemoryCredentialService()

# MongoDB Client for Users
client = pymongo.MongoClient(MONGO_URI)
try:
    db = client.get_database()
except:
    db = client["firestore-mongo"]
users_col = db["users"]

# Session Storage (Cache/State)
sessions: Dict[str, Dict[str, Any]] = {}

class AuthRequest(BaseModel):
    username: str
    password: str

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# ... (Helper functions)

class ChatRequest(BaseModel):
    message: str

@app.post("/api/signup")
async def signup(request: Request, auth_req: AuthRequest, response: Response):
    if users_col.find_one({"username": auth_req.username}):
        raise HTTPException(status_code=400, detail="User already exists")
    
    hashed_pw = _hash_password(auth_req.password)
    users_col.insert_one({
        "username": auth_req.username,
        "password": hashed_pw,
        "created_at": datetime.datetime.utcnow()
    })
    
    # Auto-login
    token = await get_or_create_session(request, user_id=auth_req.username)
    response.set_cookie(key="session_token", value=token, httponly=True)
    
    # Cache creds for Stash
    session_id = sessions[token]["session_id"]
    root_agent.stash_proxy.cache_creds(session_id, auth_req.username, auth_req.password)
    
    return {"success": True, "username": auth_req.username}

@app.post("/api/login")
async def login(request: Request, auth_req: AuthRequest, response: Response):
    user = users_col.find_one({"username": auth_req.username})
    if not user or user["password"] != _hash_password(auth_req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = await get_or_create_session(request, user_id=auth_req.username)
    response.set_cookie(key="session_token", value=token, httponly=True)
    
    # Cache creds for Stash
    session_id = sessions[token]["session_id"]
    root_agent.stash_proxy.cache_creds(session_id, auth_req.username, auth_req.password)
    
    return {"success": True, "username": auth_req.username}

@app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("session_token")
    if token and token in sessions:
        session_id = sessions[token]["session_id"]
        # Clear Stash Proxy State
        root_agent.stash_proxy.clear_creds(session_id)
        root_agent.stash_proxy.clear_remote_sid(session_id)
        del sessions[token]
    response.delete_cookie("session_token")
    return {"success": True}

@app.get("/api/me")
async def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if token and token in sessions:
        return {"username": sessions[token].get("user_id")}
    return {"username": None}

async def get_or_create_session(request: Request, user_id: Optional[str] = None) -> str:
    token = request.cookies.get("session_token")
    
    # If we have a token but it's not in memory (restart), try to recover or invalidate
    # For now, simplistic approach: if not in memory, new session.
    # Ideally should check DB for session. session_service.get_session...
    
    if not token or token not in sessions:
        token = str(uuid.uuid4())
        
    if token not in sessions:
        # Create ADK Session
        # Use provided user_id or "anon" (though we lock down chat now)
        adk_user_id = user_id if user_id else f"anon_{token[:8]}"
        
        adk_session = await session_service.create_session(
            app_name="personal_assistant_app",
            user_id=adk_user_id
        )
        
        # Create Runner
        adk_app = App(name="personal_assistant_app", root_agent=root_agent)
        runner = Runner(
            app=adk_app,
            artifact_service=artifact_service,
            session_service=session_service,
            credential_service=credential_service,
        )
        
        sessions[token] = {
            "runner": runner,
            "session_id": adk_session.id,
            "user_id": user_id
        }
    elif user_id:
        # Update user_id if logging in
        sessions[token]["user_id"] = user_id
        
    return token

@app.post("/api/chat")
async def chat(request: Request, chat_req: ChatRequest, response: Response):
    token = request.cookies.get("session_token")
    if not token or token not in sessions or not sessions[token].get("user_id"):
        raise HTTPException(status_code=401, detail="Authentication required")
        
    token = await get_or_create_session(request)
    response.set_cookie(key="session_token", value=token, httponly=True)
    
    session_data = sessions[token]
    runner = session_data["runner"]
    session_id = session_data["session_id"]
    user_id = session_data["user_id"]
    
    # Run Agent
    content = types.Content(role='user', parts=[types.Part(text=chat_req.message)])
    agent_response_text = ""
    
    try:
        async with Aclosing(
            runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=content
            )
        ) as agen:
            async for event in agen:
                if event.content and event.content.parts:
                    text = "".join(part.text or "" for part in event.content.parts)
                    agent_response_text += text
    except Exception as e:
        logger.error(f"Error executing agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"response": agent_response_text}

@app.get("/health")
async def health():
    return {"status": "ok"}

# Mount Static Files
# We mount this LAST to catch-all for SPA if needed, or just specific path
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
