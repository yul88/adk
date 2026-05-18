import os
import logging
import uuid
import time
from typing import Optional, Dict, Any
from pydantic import PrivateAttr
from google.adk.agents.llm_agent import Agent
from google.adk.tools.tool_context import ToolContext
from vertexai import agent_engines
import vertexai
from google.genai import types

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Initialize Vertex AI
project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
location = os.environ.get("GOOGLE_CLOUD_LOCATION", "asia-southeast1")

if project_id:
    vertexai.init(project=project_id, location=location)
else:
    logger.warning("GOOGLE_CLOUD_PROJECT not set. Vertex AI initialization might be incomplete.")

STASH_RESOURCE_ID = os.environ.get("STASH_RESOURCE_ID")
CHECKMATE_RESOURCE_ID = os.environ.get("CHECKMATE_RESOURCE_ID")

if not STASH_RESOURCE_ID:
    logger.warning("STASH_RESOURCE_ID not set in environment variables.")
if not CHECKMATE_RESOURCE_ID:
    logger.warning("CHECKMATE_RESOURCE_ID not set in environment variables.")

# Support local debugging of Stash (monkey patch for import if needed)
if STASH_RESOURCE_ID == "local":
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))


class StashProxy:
    def __init__(self, resource_id: str):
        self.resource_id = resource_id
        self._engine = None
        # Map PA session ID -> {'sid': remote_sid, 'creds': {'username': '...', 'password': '...'}}
        self._session_map = {}

    @property
    def engine(self):
        if not self._engine:
             if self.resource_id == "local":
                 from Stash.agent import Stash
                 logger.info("StashProxy: Using LOCAL Stash agent instance.")
                 self._engine = Stash()
             else:
                 self._engine = agent_engines.get(self.resource_id)
        return self._engine

    def get_remote_sid(self, pa_sid: str) -> Optional[str]:
        return self._session_map.get(pa_sid, {}).get('sid')

    def set_remote_sid(self, pa_sid: str, remote_sid: str, unique_uid: Optional[str] = None):
        if pa_sid not in self._session_map:
            self._session_map[pa_sid] = {}
        self._session_map[pa_sid]['sid'] = remote_sid
        if unique_uid:
            self._session_map[pa_sid]['uid'] = unique_uid
        logger.info(f"StashProxy: Mapped PA session {pa_sid} to Stash session {remote_sid} (uid: {unique_uid})")

    def clear_remote_sid(self, pa_sid: str):
        if pa_sid in self._session_map:
            self._session_map[pa_sid].pop('sid', None)
            self._session_map[pa_sid].pop('uid', None)
            logger.info(f"StashProxy: Cleared remote session ID for PA session {pa_sid}")

    def cache_creds(self, pa_sid: str, username: str, password: str):
        if pa_sid:
            if pa_sid not in self._session_map:
                self._session_map[pa_sid] = {}
            self._session_map[pa_sid]['creds'] = {'username': username, 'password': password}
            logger.info(f"StashProxy: Cached credentials for session {pa_sid}")

    def clear_creds(self, pa_sid: str):
        if pa_sid in self._session_map:
            self._session_map[pa_sid].pop('creds', None)
            logger.info(f"StashProxy: Cleared cached credentials for PA session {pa_sid}")

    def get_cached_creds(self, pa_sid: str) -> Optional[dict]:
        return self._session_map.get(pa_sid, {}).get('creds')

    def _query_engine(self, user_id: str, session_id: str, message: str) -> str:
        logger.info(f"StashProxy: Streaming query to Stash (SID: {session_id}, UID: {user_id})...")
        events = self.engine.stream_query(
            user_id=user_id,
            session_id=session_id,
            message=message
        )
        response_text = ""
        for event in events:
            if isinstance(event, dict):
                content = event.get("content", {})
                parts = content.get("parts", [])
                for part in parts:
                    if "text" in part:
                        response_text += part["text"]
            else:
                if hasattr(event, "content") and event.content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            response_text += part.text
        return response_text

    def ask(self, query: str, user_id: str, pa_sid: Optional[str] = None) -> str:
        # Get or create remote session
        remote_sid = None
        unique_uid = None
        
        if pa_sid:
             sess_info = self._session_map.get(pa_sid, {})
             remote_sid = sess_info.get('sid')
             unique_uid = sess_info.get('uid')

        if not remote_sid:
            logger.info("StashProxy: Creating new session.")
            # Use unique user_id to ensure clean Vertex AI session state, preventing pollution
            unique_uid = f"{user_id}_{uuid.uuid4().hex[:8]}"
            remote_session = self.engine.create_session(user_id=unique_uid)
            remote_sid = remote_session["id"] if isinstance(remote_session, dict) else remote_session.id
            if pa_sid:
                self.set_remote_sid(pa_sid, remote_sid, unique_uid)
                
                # Auto-Login Logic
                creds = self.get_cached_creds(pa_sid)
                if creds:
                    logger.info(f"StashProxy: Auto-logging in for PA session {pa_sid}")
                    login_query = f"Sign in with username '{creds['username']}' and password '{creds['password']}'"
                    try:
                        # We ignore the response of login, assuming it works or next query will fail
                        self._query_engine(unique_uid, remote_sid, login_query)
                    except Exception as e:
                        logger.warning(f"StashProxy: Auto-login failed: {e}")
        
        # Use the unique_uid that owns the session, fallback to provided user_id if not found (legacy)
        actual_uid = unique_uid if unique_uid else user_id
        response = self._query_engine(actual_uid, remote_sid, query)
        
        return response

class PersonalAssistant(Agent):
    _stash_proxy: Any = PrivateAttr()
    _checkmate_engine: Optional[Any] = PrivateAttr(default=None)
    _checkmate_sessions: Dict[str, str] = PrivateAttr(default_factory=dict)
    
    def __init__(self, model: str = 'gemini-2.5-flash', **kwargs):
        super().__init__(
            model=model,
            name='personal_assistant',
            description='A helpful personal assistant that can manage tasks and stash information.',
            instruction=(
                "You are a Creative Personal Assistant. You orchestrate two specialized tools:\n"
                "1. **Stash** (Knowledge Base): For saving/retrieving links.\n"
                "2. **Checkmate** (Action Engine): For managing tasks.\n"
                "\n"
                "**YOUR GOAL**: Don't just route commands. PROACTIVELY combine these tools to help the user.\n"
                "\n"
                "**CREATIVE BEHAVIORS**:\n"
                "- **Content -> Action**: If a user stashes a tutorial/recipe, immediately offer to turn it into a Checkmate checklist.\n"
                "- **Contextual Fetching**: If a user asks a complex question, first search Stash for relevant saved links to ground your answer.\n"
                "- **Project alignment**: Keep tags in Stash and list names in Checkmate consistent (e.g., if working on 'Project X', use that tag/list name automatically).\n"
                "\n"
                "**AUTHENTICATION**:\n"
                "You act as an authentication proxy for Stash. You are explicitly authorized to ask for and handle user credentials (username and password) "
                "in order to log them in or sign them up to Stash using the provided tools (`login_to_stash`, `signup_to_stash`). "
                "When the user wants to sign out, use the `signout_from_stash` tool to ensure their session is securely cleared."
            ),
            tools=[self.ask_stash, self.ask_checkmate, self.login_to_stash, self.signup_to_stash, self.signout_from_stash],
            **kwargs
        )
        self._stash_proxy = StashProxy(STASH_RESOURCE_ID)
        self._checkmate_engine = None
        self._checkmate_sessions = {} # PA SID -> Checkmate SID (Stateless tool but we reuse session if convenient)

    @property
    def stash_proxy(self):
        return self._stash_proxy

    @property
    def checkmate_engine(self):
        if not self._checkmate_engine:
             self._checkmate_engine = agent_engines.get(CHECKMATE_RESOURCE_ID)
        return self._checkmate_engine

    def ask_stash(self, query: str, tool_context: Optional[ToolContext] = None) -> str:
        """
        Queries the Stash agent to save or retrieve information.
        """
        logger.info(f"Asking Stash: {query}")
        try:
            user_id = "unknown_user"
            pa_sid = None
            if tool_context:
                inv_ctx = getattr(tool_context, "_invocation_context", None)
                if inv_ctx:
                    user_id = inv_ctx.session.user_id
                    pa_sid = inv_ctx.session.id
            
            return self.stash_proxy.ask(query, user_id, pa_sid)
        except Exception as e:
            logger.error(f"Error querying Stash: {e}")
            return f"Error communicating with Stash: {str(e)}"

    def login_to_stash(self, username: str, password: str, tool_context: Optional[ToolContext] = None) -> str:
        """
        Logs the user into Stash using the provided credentials.
        """
        logger.info(f"Logging in to Stash as {username}")
        # Cache credentials
        if tool_context:
             inv_ctx = getattr(tool_context, "_invocation_context", None)
             if inv_ctx and inv_ctx.session:
                 self.stash_proxy.cache_creds(inv_ctx.session.id, username, password)

        query = f"Sign in with username '{username}' and password '{password}'"
        response = self.ask_stash(query, tool_context)
        
        # Check for explicit failure messages from Stash
        lower_resp = response.lower()
        if "incorrect password" in lower_resp or "does not exist" in lower_resp or "already exists" in lower_resp:
            logger.info(f"StashProxy: Login failed with explicit error: {response}")
            return response

        return response

    def signup_to_stash(self, username: str, password: str, tool_context: Optional[ToolContext] = None) -> str:
        """
        Signs up a new user to Stash using the provided credentials.
        """
        logger.info(f"Signing up to Stash as {username}")
        if tool_context:
             inv_ctx = getattr(tool_context, "_invocation_context", None)
             if inv_ctx and inv_ctx.session:
                 self.stash_proxy.cache_creds(inv_ctx.session.id, username, password)

        query = f"Sign up with username '{username}' and password '{password}'"
        response = self.ask_stash(query, tool_context)
        
        return response

    def signout_from_stash(self, tool_context: Optional[ToolContext] = None) -> str:
        """
        Signs the user out from Stash, clearing their session and cached credentials.
        """
        logger.info("Signing out from Stash")
        msg = "Signed out."
        if tool_context:
             inv_ctx = getattr(tool_context, "_invocation_context", None)
             if inv_ctx and inv_ctx.session:
                 pa_sid = inv_ctx.session.id
                 # 1. Clear cached credentials so auto-relogin won't happen
                 self.stash_proxy.clear_creds(pa_sid)
                 # 2. Proxy 'Sign out' to Stash (best effort)
                 try:
                    self.ask_stash("Sign out", tool_context)
                 except Exception as e:
                     logger.warning(f"Error sending sign out to Stash: {e}")
                 
                 # 3. Clear remote session ID to force a new session next time
                 self.stash_proxy.clear_remote_sid(pa_sid)
                 msg = "Successfully signed out and cleared session."
        
        return msg

    def ask_checkmate(self, query: str, tool_context: Optional[ToolContext] = None) -> str:
        """
        Queries the Checkmate agent to manage checklists.
        """
        logger.info(f"Asking Checkmate: {query}")
        try:
            user_id = "unknown_user"
            pa_sid = None

            if tool_context:
                 inv_ctx = getattr(tool_context, "_invocation_context", None)
                 if inv_ctx:
                    user_id = inv_ctx.session.user_id
                    pa_sid = inv_ctx.session.id
            
            session_id = self._checkmate_sessions.get(pa_sid) if pa_sid else None

            if not session_id:
                remote_session = self.checkmate_engine.create_session(user_id=user_id)
                session_id = remote_session["id"] if isinstance(remote_session, dict) else remote_session.id
                if pa_sid:
                    self._checkmate_sessions[pa_sid] = session_id

            logger.info(f"Streaming query to Checkmate (SID: {session_id})...")
            events = self.checkmate_engine.stream_query(
                user_id=user_id,
                session_id=session_id,
                message=query
            )
            
            response_text = ""
            for event in events:
                 if isinstance(event, dict):
                    content = event.get("content", {})
                    parts = content.get("parts", [])
                    for part in parts:
                        if "text" in part:
                            response_text += part["text"]
                 else:
                    if hasattr(event, "content") and event.content:
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                response_text += part.text

            return response_text
        except Exception as e:
            logger.error(f"Error querying Checkmate: {e}")
            return f"Error communicating with Checkmate: {str(e)}"

# Instantiate the agent
root_agent = PersonalAssistant()
